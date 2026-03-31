import os
from datetime import datetime, timedelta, timezone

import httpx
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

# 导入调度器管理器
from scheduler import SchedulerManager


@register("astrbot_plugin_xmonitor", "united_pooh", "一个api调用器", "1.0.0")
class XMonitor(Star):
    DEFAULT_TARGET_ACCOUNT = "elonmusk"
    DEFAULT_CHECK_INTERVAL = 600
    DEFAULT_LOOKBACK = timedelta(minutes=15)
    KV_LAST_CHECKED_AT = "twitter_monitor_last_checked_at"
    KV_LAST_TWEET_IDS = "twitter_monitor_last_tweet_ids"

    def __init__(self, context: Context):
        super().__init__(context)
        self.scheduler = SchedulerManager()
        self.job_id = None
        self.api_key = None
        self.target_account = self.DEFAULT_TARGET_ACCOUNT
        self.check_interval = self.DEFAULT_CHECK_INTERVAL
        self.last_checked_time = None

    async def initialize(self):
        """初始化插件并按固定间隔调度 Twitter 轮询任务。"""
        self.api_key = self._get_api_key()
        self.target_account = self._get_config_value("TARGET_ACCOUNT", self.DEFAULT_TARGET_ACCOUNT)
        self.check_interval = int(self._get_config_value("CHECK_INTERVAL", self.DEFAULT_CHECK_INTERVAL))

        last_checked_raw = await self.get_kv_data(self.KV_LAST_CHECKED_AT, None)
        self.last_checked_time = self._parse_datetime(last_checked_raw)
        if self.last_checked_time is None:
            self.last_checked_time = datetime.now(timezone.utc) - self.DEFAULT_LOOKBACK

        await self.get_latest_tweet()

        self.job_id = self.scheduler.add_job(
            self.get_latest_tweet,
            "interval",
            seconds=self.check_interval,
            max_instances=1,
            coalesce=True,
        )
        if self.job_id:
            logger.info(
                f"成功安排任务 'get_latest_tweet'，ID为: {self.job_id}，"
                f"账号: @{self.target_account}，间隔: {self.check_interval}s"
            )

    def _get_config_value(self, key: str, default):
        cfg = self.context.get_config()
        if cfg is None:
            return default
        value = cfg.get(key, default) if hasattr(cfg, "get") else default
        return default if value in (None, "") else value

    def _get_api_key(self) -> str:
        cfg_key = self._get_config_value("X-API", "")
        env_key = os.getenv("TWITTERAPI_IO_API_KEY", "")
        api_key = cfg_key or env_key
        if isinstance(api_key, str) and api_key.strip().lower() == "your api key":
            api_key = ""
        if not api_key:
            logger.warning(
                "未找到 TwitterAPI.io API key。请在 AstrBot 配置中设置 `X-API`，"
                "或设置环境变量 `TWITTERAPI_IO_API_KEY`。"
            )
        return api_key

    @staticmethod
    def _parse_datetime(value):
        if not value:
            return None
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc)
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed.astimezone(timezone.utc)
            except ValueError:
                return None
        return None

    @staticmethod
    def _format_search_time(value: datetime) -> str:
        return value.astimezone(timezone.utc).strftime("%Y-%m-%d_%H:%M:%S_UTC")

    async def _fetch_new_tweets(self):
        """从 TwitterAPI.io 获取增量推文。"""
        if not self.api_key:
            raise RuntimeError("缺少 TwitterAPI.io API key")

        until_time = datetime.now(timezone.utc)
        since_time = self.last_checked_time or (until_time - self.DEFAULT_LOOKBACK)
        since_str = self._format_search_time(since_time)
        until_str = self._format_search_time(until_time)

        query = (
            f"from:{self.target_account} "
            f"since:{since_str} until:{until_str} include:nativeretweets"
        )
        url = "https://api.twitterapi.io/twitter/tweet/advanced_search"
        params = {"query": query, "queryType": "Latest"}
        headers = {"X-API-Key": self.api_key}

        tweets = []
        seen_ids = set()
        next_cursor = None

        async with httpx.AsyncClient(timeout=30) as client:
            while True:
                request_params = dict(params)
                if next_cursor:
                    request_params["cursor"] = next_cursor

                response = await client.get(url, headers=headers, params=request_params)
                response.raise_for_status()
                data = response.json()

                page_tweets = data.get("tweets", []) or []
                for tweet in page_tweets:
                    tweet_id = (
                        tweet.get("id")
                        or tweet.get("tweet_id")
                        or tweet.get("rest_id")
                        or tweet.get("tweetId")
                    )
                    if tweet_id and tweet_id in seen_ids:
                        continue
                    if tweet_id:
                        seen_ids.add(tweet_id)
                    tweets.append(tweet)

                if data.get("has_next_page") and data.get("next_cursor"):
                    next_cursor = data.get("next_cursor")
                    continue
                break

        tweets.sort(key=lambda item: item.get("createdAt", ""))
        self.last_checked_time = until_time
        await self.put_kv_data(self.KV_LAST_CHECKED_AT, until_time.isoformat())
        await self.put_kv_data(
            self.KV_LAST_TWEET_IDS,
            [tweet.get("id") or tweet.get("tweet_id") or tweet.get("rest_id") for tweet in tweets if tweet],
        )
        return tweets

    @filter.command("new")
    async def get_latest_tweet_command(self, event: AstrMessageEvent):
        """手动触发获取最新推文的命令。"""
        try:
            data = await self._fetch_new_tweets()
            if not data:
                yield event.plain_result(f"@{self.target_account} 在最近窗口内没有新推文。")
                return

            lines = [f"找到 {len(data)} 条推文："]
            for tweet in data:
                created_at = tweet.get("createdAt", "unknown time")
                text = tweet.get("text", "").replace("\n", " ").strip()
                lines.append(f"[{created_at}] {text}")
            yield event.plain_result("\n".join(lines))
        except Exception as e:
            yield event.plain_result(f"请求失败: {e}")

    async def get_latest_tweet(self):
        """用于获取最新推文并记录的计划任务。"""
        try:
            data = await self._fetch_new_tweets()
            if not data:
                logger.info(f"@{self.target_account} 最近没有新推文。")
                return

            logger.info(f"发现 {len(data)} 条来自 @{self.target_account} 的新推文。")
            for tweet in data:
                created_at = tweet.get("createdAt", "unknown time")
                text = tweet.get("text", "").replace("\n", " ").strip()
                logger.info(f"[{created_at}] {text}")
        except Exception as e:
            logger.error(f"计划任务 'get_latest_tweet' 失败: {e}")

    async def terminate(self):
        """通过取消计划任务来清理插件。"""
        if self.job_id:
            self.scheduler.cancel_job(self.job_id)
            logger.info(f"成功取消了任务 'get_latest_tweet'，ID为: {self.job_id}")
