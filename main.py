from datetime import datetime, timedelta, timezone

import httpx
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.api import logger
from astrbot.api import AstrBotConfig
from astrbot.core.message.message_event_result import MessageChain

# 导入调度器管理器
from scheduler import SchedulerManager


@register("astrbot_plugin_xmonitor", "united_pooh", "一个api调用器", "1.0.0")
class XMonitor(Star):
    KV_LAST_CHECKED_AT = "twitter_monitor_last_checked_at"
    KV_LAST_TWEET_IDS = "twitter_monitor_last_tweet_ids"

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.scheduler = SchedulerManager()
        self.config = config
        self.job_id = None
        self.api_key = self.config.get("X-API", "")
        self.target_account = self.config.get("TARGET_ACCOUNT")
        self.check_interval = int(self.config.get("CHECK_INTERVAL"))
        self.subscribe_groups = self._normalize_subscribe_groups(
            self.config.get("SUBSCRIBE_GROUPS", [])
        )
        self.last_checked_time = None

    async def initialize(self):
        """初始化插件并按固定间隔调度 Twitter 轮询任务。"""
        self.api_key = self.config.get("X-API", "")
        self.target_account = self.config.get("TARGET_ACCOUNT")
        self.check_interval = int(self.config.get("CHECK_INTERVAL"))
        self.subscribe_groups = self._normalize_subscribe_groups(
            self.config.get("SUBSCRIBE_GROUPS", [])
        )
        interval_minutes = self._get_aligned_interval_minutes()

        last_checked_raw = await self.get_kv_data(self.KV_LAST_CHECKED_AT, None)
        self.last_checked_time = self._parse_datetime(last_checked_raw)

        self.job_id = self.scheduler.add_job(
            self.check_for_new_tweets,
            "cron",
            minute=f"*/{interval_minutes}",
            second=0,
            max_instances=1,
            coalesce=True,
        )
        if self.job_id:
            logger.info(
                f"成功安排任务 'check_for_new_tweets'，ID为: {self.job_id}，"
                f"账号: @{self.target_account}，将在每个整{interval_minutes}分钟执行一次"
            )
            if self.subscribe_groups:
                logger.info(
                    f"定时通知已启用，将向 {len(self.subscribe_groups)} 个群广播更新。"
                )
            else:
                logger.warning("SUBSCRIBE_GROUPS 为空，定时任务发现新推文时不会主动推送。")

    @staticmethod
    def _normalize_subscribe_groups(raw_value) -> list[str]:
        """将配置中的订阅群组统一转换为去重后的字符串列表。"""
        if raw_value is None:
            return []

        if isinstance(raw_value, str):
            parts = raw_value.replace("\n", ",").split(",")
        elif isinstance(raw_value, list):
            parts = raw_value
        else:
            parts = [raw_value]

        groups: list[str] = []
        seen: set[str] = set()
        for item in parts:
            group_id = str(item).strip()
            if not group_id or group_id in seen:
                continue
            seen.add(group_id)
            groups.append(group_id)
        return groups

    def _get_aligned_interval_minutes(self) -> int:
        if self.check_interval <= 0:
            raise ValueError("CHECK_INTERVAL 必须大于 0")
        if self.check_interval % 60 != 0:
            raise ValueError("CHECK_INTERVAL 必须是 60 秒的整数倍")

        interval_minutes = self.check_interval // 60
        if 60 % interval_minutes != 0:
            raise ValueError("CHECK_INTERVAL 必须能整除 60 分钟，才能对齐整点分钟")
        return interval_minutes

    def _get_aligned_checkpoint_time(self) -> datetime:
        now = datetime.now(timezone.utc)
        interval_minutes = self._get_aligned_interval_minutes()
        aligned_minute = (now.minute // interval_minutes) * interval_minutes
        return now.replace(minute=aligned_minute, second=0, microsecond=0)

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

    @staticmethod
    def _extract_tweet_id(tweet: dict) -> str | None:
        tweet_id = (
            tweet.get("id")
            or tweet.get("tweet_id")
            or tweet.get("rest_id")
            or tweet.get("tweetId")
        )
        if tweet_id is None:
            return None
        return str(tweet_id)

    @staticmethod
    def _sanitize_tweet_text(tweet: dict) -> str:
        text = str(tweet.get("text", "")).replace("\n", " ").strip()
        return " ".join(text.split()) or "(无正文)"

    def _build_notification_message(self, tweets: list[dict]) -> str:
        lines = [f"@{self.target_account} 有 {len(tweets)} 条新推文："]
        for tweet in tweets:
            created_at = tweet.get("createdAt", "unknown time")
            lines.append(f"[{created_at}] {self._sanitize_tweet_text(tweet)}")
            tweet_id = self._extract_tweet_id(tweet)
            if tweet_id:
                lines.append(f"https://x.com/{self.target_account}/status/{tweet_id}")
        return "\n".join(lines)

    async def notify_subscribers(self, tweets: list[dict]) -> None:
        """将新推文主动广播给配置中的所有订阅群。"""
        if not tweets:
            return
        if not self.subscribe_groups:
            logger.warning("未配置 SUBSCRIBE_GROUPS，跳过主动推送。")
            return

        chain = MessageChain().message(self._build_notification_message(tweets))
        for group_id in self.subscribe_groups:
            try:
                await StarTools.send_message_by_id(
                    type="GroupMessage",
                    id=group_id,
                    message_chain=chain,
                )
                logger.info(f"向群组 {group_id} 推送 @{self.target_account} 新推文成功")
            except Exception as e:
                logger.error(f"向群组 {group_id} 推送 @{self.target_account} 新推文失败: {e}")

    async def _fetch_new_tweets(
        self, *, update_checkpoint: bool, until_time: datetime | None = None
    ):
        """从 TwitterAPI.io 获取增量推文。"""
        if not self.api_key:
            raise RuntimeError("缺少 TwitterAPI.io API key")

        until_time = until_time or datetime.now(timezone.utc)
        since_time = self.last_checked_time or (
            until_time - timedelta(seconds=self.check_interval)
        )
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
                    tweet_id = self._extract_tweet_id(tweet)
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
        if update_checkpoint:
            self.last_checked_time = until_time
            await self.put_kv_data(self.KV_LAST_CHECKED_AT, until_time.isoformat())
            await self.put_kv_data(
                self.KV_LAST_TWEET_IDS,
                [
                    tweet_id
                    for tweet in tweets
                    if (tweet_id := self._extract_tweet_id(tweet))
                ],
            )
        return tweets

    @filter.command("new")
    async def get_latest_tweet_command(self, event: AstrMessageEvent):
        """手动触发获取最新推文的命令。"""
        try:
            data = await self._fetch_new_tweets(update_checkpoint=False)
            if not data:
                yield event.plain_result(
                    f"@{self.target_account} 在最近窗口内没有新推文。"
                )
                return

            lines = [f"找到 {len(data)} 条推文："]
            for tweet in data:
                created_at = tweet.get("createdAt", "unknown time")
                text = tweet.get("text", "").replace("\n", " ").strip()
                lines.append(f"[{created_at}] {text}")
            yield event.plain_result("\n".join(lines))
        except Exception as e:
            yield event.plain_result(f"请求失败: {e}")

    async def check_for_new_tweets(self):
        """用于获取最新推文并记录的计划任务。"""
        try:
            data = await self._fetch_new_tweets(
                update_checkpoint=True,
                until_time=self._get_aligned_checkpoint_time(),
            )
            if not data:
                logger.info(f"@{self.target_account} 最近没有新推文。")
                return

            logger.info(f"发现 {len(data)} 条来自 @{self.target_account} 的新推文。")
            await self.notify_subscribers(data)
            for tweet in data:
                created_at = tweet.get("createdAt", "unknown time")
                text = self._sanitize_tweet_text(tweet)
                logger.info(f"[{created_at}] {text}")
        except Exception as e:
            logger.error(f"计划任务 'check_for_new_tweets' 失败: {e}")

    async def terminate(self):
        """通过取消计划任务来清理插件。"""
        if self.job_id:
            self.scheduler.cancel_job(self.job_id)
            logger.info(f"成功取消了任务 'check_for_new_tweets'，ID为: {self.job_id}")
