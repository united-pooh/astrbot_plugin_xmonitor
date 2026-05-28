import asyncio
import base64
import binascii
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import httpx
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core.message.message_event_result import MessageChain

# 导入调度器管理器
try:
    from .scheduler import SchedulerManager
except ImportError:  # pragma: no cover - local script fallback for lightweight probes.
    from scheduler import SchedulerManager

try:
    from .tweet_renderer import render_to_base64
except ImportError:  # pragma: no cover - local script fallback for lightweight probes.
    from tweet_renderer import render_to_base64

try:
    from .history_store import TweetHistoryLookupCollision, TweetHistoryStore
except ImportError:  # pragma: no cover - local script fallback for lightweight probes.
    from history_store import TweetHistoryLookupCollision, TweetHistoryStore


PLUGIN_DIR = Path(__file__).resolve().parent
DEFAULT_FONT_DIR = PLUGIN_DIR / "data" / "fonts"
DEFAULT_FONT_DOWNLOADS = (
    (
        "NotoSansCJKsc-Regular.otf",
        "https://raw.githubusercontent.com/notofonts/noto-cjk/main/"
        "Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf",
    ),
    (
        "NotoSansCJKsc-Bold.otf",
        "https://raw.githubusercontent.com/notofonts/noto-cjk/main/"
        "Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Bold.otf",
    ),
)
DEFAULT_EMOJI_FONT_DOWNLOADS = (
    (
        "NotoColorEmoji.ttf",
        "https://raw.githubusercontent.com/googlefonts/noto-emoji/main/"
        "fonts/NotoColorEmoji.ttf",
    ),
)


def _download_font_file(url: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    try:
        request = Request(url, headers={"User-Agent": "xmonitor-font-bootstrap/1.0"})
        with urlopen(request, timeout=120) as response:
            status = getattr(response, "status", 200)
            if status >= 400:
                raise RuntimeError(f"下载字体失败，HTTP {status}: {url}")
            with temp_path.open("wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
        if temp_path.stat().st_size < 1024 * 1024:
            raise RuntimeError(f"下载的字体文件过小，可能不是有效字体: {url}")
        temp_path.replace(output_path)
    except (OSError, URLError, TimeoutError) as error:
        raise RuntimeError(f"下载字体失败: {url}: {error}") from error
    finally:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError:
            pass


@register(
    "astrbot_plugin_xmonitor",
    "united_pooh",
    "监控 X/Twitter 账号并渲染推文图片",
    "1.0.1",
)
class XMonitor(Star):
    TWITTER_SEARCH_URL = "https://api.twitterapi.io/twitter/tweet/advanced_search"
    TWITTER_USER_INFO_URL = "https://api.twitterapi.io/twitter/user/info"

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.scheduler = SchedulerManager()
        self.config = config
        self.job_id = None
        self.api_key = self.config.get("X-API", "")
        self.target_account = self.config.get("TARGET_ACCOUNT")
        self.check_interval_minutes = self._validate_check_interval_minutes(
            self.config.get("CHECK_INTERVAL")
        )
        self.subscribe_groups = self._normalize_subscribe_groups(
            self.config.get("SUBSCRIBE_GROUPS", [])
        )
        self.notify_user = self._normalize_notify_user(
            self.config.get("NOTIFY_USER", "")
        )
        self.source_logo = self._normalize_source_logo(
            self.config.get("SOURCE_LOGO", "assets/source_logo.png")
        )
        self._refresh_render_font_settings()
        self._font_bootstrap_task: asyncio.Task | None = None
        self.history_store = TweetHistoryStore(self._history_db_path())

    async def initialize(self):
        """初始化插件并按分钟间隔调度 Twitter 轮询任务。"""
        self.api_key = self.config.get("X-API", "")
        self.target_account = self.config.get("TARGET_ACCOUNT")
        self.check_interval_minutes = self._validate_check_interval_minutes(
            self.config.get("CHECK_INTERVAL")
        )
        self.subscribe_groups = self._normalize_subscribe_groups(
            self.config.get("SUBSCRIBE_GROUPS", [])
        )
        self.notify_user = self._normalize_notify_user(
            self.config.get("NOTIFY_USER", "")
        )
        self.source_logo = self._normalize_source_logo(
            self.config.get("SOURCE_LOGO", "assets/source_logo.png")
        )
        self._refresh_render_font_settings()
        self._start_font_bootstrap_task()

        self.job_id = self.scheduler.add_job(
            self.check_for_new_tweets,
            "interval",
            minutes=self.check_interval_minutes,
            max_instances=1,
            coalesce=True,
        )
        if self.job_id:
            logger.info(
                f"成功安排任务 'check_for_new_tweets'，ID为: {self.job_id}，"
                f"账号: @{self.target_account}，将在插件启动后每 {self.check_interval_minutes} 分钟执行一次"
            )
            if self.subscribe_groups:
                logger.info(
                    f"定时通知已启用，将向 {len(self.subscribe_groups)} 个群广播更新。"
                )
            else:
                logger.warning(
                    "SUBSCRIBE_GROUPS 为空，定时任务发现新推文时不会主动推送。"
                )

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

    @staticmethod
    def _normalize_notify_user(raw_value) -> str | None:
        """将配置中的通知用户转换为可用于 at 的用户 ID。"""
        if raw_value is None:
            return None
        notify_user = str(raw_value).strip()
        return notify_user or None

    @staticmethod
    def _normalize_source_logo(raw_value) -> str | None:
        """将来源 logo 路径解析为插件目录内的绝对路径。"""
        if raw_value is None:
            return None
        logo_path_text = str(raw_value).strip()
        if not logo_path_text:
            return None
        logo_path = Path(logo_path_text).expanduser()
        if not logo_path.is_absolute():
            logo_path = Path(__file__).resolve().parent / logo_path
        return str(logo_path)

    @staticmethod
    def _normalize_bool(raw_value, default: bool = True) -> bool:
        if raw_value is None:
            return default
        if isinstance(raw_value, bool):
            return raw_value
        text = str(raw_value).strip().lower()
        if not text:
            return default
        return text not in {"0", "false", "no", "off", "关闭", "否"}

    @staticmethod
    def _normalize_path_list(raw_value) -> list[str]:
        if raw_value is None:
            return []
        if isinstance(raw_value, str):
            parts = re.split(r"[\n,;]+", raw_value)
        elif isinstance(raw_value, list):
            parts = raw_value
        else:
            parts = [raw_value]

        paths: list[str] = []
        seen: set[str] = set()
        for item in parts:
            path_text = str(item).strip()
            if not path_text:
                continue
            path = Path(path_text).expanduser()
            if not path.is_absolute():
                path = PLUGIN_DIR / path
            resolved = str(path)
            if resolved in seen:
                continue
            seen.add(resolved)
            paths.append(resolved)
        return paths

    def _refresh_render_font_settings(self) -> None:
        self.auto_download_fonts = self._normalize_bool(
            self.config.get("AUTO_DOWNLOAD_FONTS", True),
            default=True,
        )
        self.font_paths = self._normalize_path_list(self.config.get("FONT_PATHS"))
        self.bold_font_paths = self._normalize_path_list(
            self.config.get("BOLD_FONT_PATHS")
        )
        self.emoji_font_paths = self._normalize_path_list(
            self.config.get("EMOJI_FONT_PATHS")
        )
        auto_paths = [DEFAULT_FONT_DIR / name for name, _url in DEFAULT_FONT_DOWNLOADS]
        self.auto_font_paths = [str(path) for path in auto_paths]
        self.auto_bold_font_paths = [
            str(path) for path in auto_paths if "bold" in path.name.lower()
        ]
        self.auto_emoji_font_paths = [
            str(DEFAULT_FONT_DIR / name)
            for name, _url in DEFAULT_EMOJI_FONT_DOWNLOADS
        ]

    def _start_font_bootstrap_task(self) -> None:
        self._font_bootstrap_task = None
        if not self.auto_download_fonts:
            return
        missing = [
            (name, url)
            for name, url in (*DEFAULT_FONT_DOWNLOADS, *DEFAULT_EMOJI_FONT_DOWNLOADS)
            if not (DEFAULT_FONT_DIR / name).exists()
        ]
        if not missing:
            return
        self._font_bootstrap_task = asyncio.create_task(
            self._ensure_render_fonts(missing),
            name="xmonitor-font-bootstrap",
        )

    async def _ensure_render_fonts(self, downloads: list[tuple[str, str]]) -> None:
        logger.info(
            f"检测到渲染字体缺失，开始后台下载 {len(downloads)} 个 Noto Sans CJK 字体文件。"
        )
        for name, url in downloads:
            output_path = DEFAULT_FONT_DIR / name
            if output_path.exists():
                continue
            try:
                await asyncio.to_thread(_download_font_file, url, output_path)
                logger.info(f"渲染字体已下载: {output_path}")
            except Exception as error:
                logger.error(f"渲染字体下载失败 {name}: {error}")
        logger.info("渲染字体后台检查完成。")

    async def _wait_for_font_bootstrap(self) -> None:
        task = getattr(self, "_font_bootstrap_task", None)
        if task is None or task.done():
            return
        logger.info("渲染字体仍在下载，等待后台字体任务完成后继续生成图片。")
        await task

    @staticmethod
    def _history_db_path() -> Path:
        return PLUGIN_DIR / "data" / "tweet_history.sqlite3"

    @staticmethod
    def _validate_check_interval_minutes(raw_value) -> int:
        interval_minutes = int(raw_value)
        if interval_minutes <= 0:
            raise ValueError("CHECK_INTERVAL 必须大于 0，单位为分钟")
        return interval_minutes

    def _target_account_name(self) -> str:
        return str(getattr(self, "target_account", "") or "").strip().lstrip("@")

    def _build_search_query(self) -> str:
        target_account = self._target_account_name()
        if not target_account:
            raise RuntimeError("缺少 TARGET_ACCOUNT")

        return (
            f"from:{target_account} "
            "include:nativeretweets "
            f"within_time:{self.check_interval_minutes}m"
        )

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

    @staticmethod
    def _parse_tweet_datetime(tweet: dict) -> datetime | None:
        raw_value = tweet.get("createdAt")
        if not raw_value:
            return None

        if isinstance(raw_value, datetime):
            parsed = raw_value
        else:
            raw_text = str(raw_value).strip()
            try:
                parsed = datetime.strptime(raw_text, "%a %b %d %H:%M:%S %z %Y")
            except ValueError:
                try:
                    parsed = datetime.fromisoformat(raw_text.replace("Z", "+00:00"))
                except ValueError:
                    return None

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone(timedelta(hours=8)))

    def _format_created_at(self, tweet: dict) -> str:
        parsed = self._parse_tweet_datetime(tweet)
        if parsed is None:
            return str(tweet.get("createdAt", "unknown time"))
        return parsed.strftime("%Y-%m-%d %H:%M:%S")

    def _build_tweet_display_lines(
        self, tweet: dict, *, include_link: bool
    ) -> list[str]:
        lines = [
            self._format_created_at(tweet),
            self._sanitize_tweet_text(tweet),
        ]
        tweet_id = self._extract_tweet_id(tweet)
        if include_link and tweet_id:
            lines.append(f"https://x.com/{self.target_account}/status/{tweet_id}")
        return lines

    def _build_notification_message(self, tweets: list[dict]) -> str:
        lines = [f"@{self.target_account} 有 {len(tweets)} 条新推文："]
        for index, tweet in enumerate(tweets):
            if index > 0:
                lines.append("")
            lines.extend(self._build_tweet_display_lines(tweet, include_link=True))
        return "\n".join(lines)

    def _build_text_message_chain(self, tweets: list[dict]) -> MessageChain:
        chain = MessageChain()
        if self.notify_user:
            chain = chain.at(str(self.notify_user), self.notify_user)
        return chain.message(self._build_notification_message(tweets))

    def _build_tweet_image_message_chain(self, image_base64: str) -> MessageChain:
        chain = MessageChain()
        if self.notify_user:
            chain = chain.at(str(self.notify_user), self.notify_user)
        return chain.base64_image(image_base64)

    def _build_render_options(
        self,
        extra_options: dict | None = None,
        *,
        avatar_account: str | None = None,
    ) -> dict | None:
        render_options = {}
        source_logo = getattr(self, "source_logo", None)
        if source_logo:
            render_options["source_logo"] = source_logo
        cached_avatar = self._cached_avatar_image_source(avatar_account)
        if cached_avatar is not None:
            render_options["avatar"] = cached_avatar
        font_paths = list(getattr(self, "font_paths", []))
        bold_font_paths = list(getattr(self, "bold_font_paths", []))
        emoji_font_paths = list(getattr(self, "emoji_font_paths", []))
        if getattr(self, "auto_download_fonts", False):
            font_paths.extend(getattr(self, "auto_font_paths", []))
            bold_font_paths.extend(getattr(self, "auto_bold_font_paths", []))
            emoji_font_paths.extend(getattr(self, "auto_emoji_font_paths", []))
        if font_paths:
            render_options["font_paths"] = font_paths
        if bold_font_paths:
            render_options["bold_font_paths"] = bold_font_paths
        if emoji_font_paths:
            render_options["emoji_font_paths"] = emoji_font_paths
        if extra_options:
            render_options.update(extra_options)
        return render_options or None

    def _cached_avatar_image_source(self, account: str | None = None) -> bytes | None:
        account = str(account or self._target_account_name()).strip().lstrip("@")
        history_store = getattr(self, "history_store", None)
        if not account or history_store is None:
            return None
        try:
            record = history_store.get_user_avatar(account)
        except Exception as error:
            logger.error(f"读取 @{account} 头像缓存失败: {error}")
            return None
        if record is None or not record.avatar_base64:
            return None
        try:
            return base64.b64decode(record.avatar_base64, validate=True)
        except (binascii.Error, ValueError) as error:
            logger.error(f"@{account} 头像缓存 base64 无效: {error}")
            return None

    async def _render_tweet_to_base64(
        self,
        tweet: dict,
        extra_options: dict | None = None,
        *,
        avatar_account: str | None = None,
    ) -> str:
        await self._wait_for_font_bootstrap()
        return await asyncio.to_thread(
            render_to_base64,
            tweet,
            self._build_render_options(extra_options, avatar_account=avatar_account),
        )

    async def _send_text_fallback(
        self,
        group_id: str,
        tweet: dict,
        reason: Exception,
    ) -> None:
        try:
            await StarTools.send_message_by_id(
                type="GroupMessage",
                id=group_id,
                message_chain=self._build_text_message_chain([tweet]),
            )
            logger.info(
                f"已向群组 {group_id} 发送 @{self.target_account} 新推文纯文本 fallback"
            )
        except Exception as fallback_error:
            logger.error(
                f"向群组 {group_id} 发送 @{self.target_account} 纯文本 fallback 失败: "
                f"{fallback_error}; 原始错误: {reason}"
            )

    async def notify_subscribers(self, tweets: list[dict]) -> None:
        """将新推文主动广播给配置中的所有订阅群。"""
        if not tweets:
            return
        if not self.subscribe_groups:
            logger.warning("未配置 SUBSCRIBE_GROUPS，跳过主动推送。")
            return

        rendered_tweets: list[tuple[dict, str | None, Exception | None]] = []

        for tweet in tweets:
            try:
                rendered_base64 = await self._render_tweet_to_base64(tweet)
                rendered_tweets.append((tweet, rendered_base64, None))
            except Exception as error:
                logger.error(
                    f"渲染 @{self.target_account} 新推文图片失败，改用纯文本: {error}"
                )
                rendered_tweets.append((tweet, None, error))

        for group_id in self.subscribe_groups:
            for tweet, image_base64, render_exception in rendered_tweets:
                if render_exception is not None:
                    await self._send_text_fallback(group_id, tweet, render_exception)
                    continue
                if image_base64 is None:
                    await self._send_text_fallback(
                        group_id,
                        tweet,
                        RuntimeError("推文图片渲染结果为空"),
                    )
                    continue

                try:
                    chain = self._build_tweet_image_message_chain(image_base64)
                    await StarTools.send_message_by_id(
                        type="GroupMessage",
                        id=group_id,
                        message_chain=chain,
                    )
                    logger.info(
                        f"向群组 {group_id} 推送 @{self.target_account} 新推文图片成功"
                    )
                except Exception as send_error:
                    logger.error(
                        f"向群组 {group_id} 推送 @{self.target_account} 新推文图片失败: "
                        f"{send_error}"
                    )
                    await self._send_text_fallback(group_id, tweet, send_error)

    def _dedupe_tweets(self, tweets: list[dict], seen_ids: set[str]) -> list[dict]:
        unique_tweets = []
        for tweet in tweets:
            tweet_id = self._extract_tweet_id(tweet)
            if tweet_id and tweet_id in seen_ids:
                continue
            if tweet_id:
                seen_ids.add(tweet_id)
            unique_tweets.append(tweet)
        return unique_tweets

    async def _fetch_tweet_search_window(
        self,
        client: httpx.AsyncClient,
        cursor: str | None = None,
    ) -> dict:
        query = self._build_search_query()
        params = {"query": query, "queryType": "Latest"}
        if cursor:
            params["cursor"] = cursor
        response = await client.get(
            self.TWITTER_SEARCH_URL,
            headers={"X-API-Key": self.api_key},
            params=params,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("status") == "error":
            message = data.get("message") or data.get("msg") or "unknown error"
            raise RuntimeError(f"TwitterAPI.io advanced_search 返回错误: {message}")
        return data

    async def _ensure_target_avatar_cached(
        self,
        client: httpx.AsyncClient,
        account: str | None = None,
    ) -> None:
        account = str(account or self._target_account_name()).strip().lstrip("@")
        if not account:
            return
        try:
            existing = self.history_store.get_user_avatar(account)
            if existing is not None and existing.avatar_base64:
                return

            profile_picture_url = await self._fetch_profile_picture_url(
                client,
                account,
            )
            if not profile_picture_url:
                logger.warning(f"@{account} 用户资料中没有 profilePicture，跳过头像缓存。")
                return

            avatar_bytes = await self._download_avatar_bytes(profile_picture_url)
            avatar_base64 = base64.b64encode(avatar_bytes).decode("ascii")
            self.history_store.save_user_avatar(
                account,
                profile_picture_url=profile_picture_url,
                avatar_base64=avatar_base64,
            )
            logger.info(f"@{account} 用户头像已缓存到本地数据库。")
        except Exception as error:
            logger.error(f"@{account} 用户头像缓存失败，继续获取推文: {error}")

    async def _fetch_profile_picture_url(
        self,
        client: httpx.AsyncClient,
        account: str,
    ) -> str | None:
        response = await client.get(
            self.TWITTER_USER_INFO_URL,
            headers={"X-API-Key": self.api_key},
            params={"userName": account},
        )
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict) and data.get("status") == "error":
            message = data.get("message") or data.get("msg") or "unknown error"
            raise RuntimeError(f"TwitterAPI.io user/info 返回错误: {message}")
        return self._extract_profile_picture_url(data)

    @staticmethod
    def _extract_profile_picture_url(data) -> str | None:
        if not isinstance(data, dict):
            return None
        containers = [data]
        for key in ("data", "user", "userInfo"):
            value = data.get(key)
            if isinstance(value, dict):
                containers.append(value)
        for container in containers:
            for key in (
                "profilePicture",
                "profile_picture",
                "profileImageUrl",
                "profile_image_url",
                "profile_image_url_https",
                "avatar",
            ):
                value = container.get(key)
                if value:
                    return str(value)
        return None

    async def _download_avatar_bytes(self, profile_picture_url: str) -> bytes:
        if not self._is_valid_avatar_url(profile_picture_url):
            raise ValueError("profilePicture 不是有效的 http(s) URL")
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            response = await client.get(profile_picture_url)
            response.raise_for_status()
            content = bytes(response.content)
        if not content:
            raise RuntimeError("头像下载结果为空")
        if len(content) > 5 * 1024 * 1024:
            raise RuntimeError("头像文件超过 5 MiB，拒绝写入数据库")
        return content

    @staticmethod
    def _is_valid_avatar_url(profile_picture_url: str) -> bool:
        parsed = urlparse(str(profile_picture_url))
        host = (parsed.hostname or "").strip().lower()
        return (
            parsed.scheme in {"http", "https"}
            and bool(host)
            and host != "localhost"
            and not host.endswith(".localhost")
        )

    async def _fetch_new_tweets(self):
        """从 TwitterAPI.io 获取最近 CHECK_INTERVAL 分钟内的推文。"""
        if not self.api_key:
            raise RuntimeError("缺少 TwitterAPI.io API key")

        seen_ids = set()

        tweets = []
        next_cursor = None

        async with httpx.AsyncClient(timeout=30) as client:
            await self._ensure_target_avatar_cached(client)
            while True:
                data = await self._fetch_tweet_search_window(client, next_cursor)
                tweets.extend(
                    self._dedupe_tweets(data.get("tweets", []) or [], seen_ids)
                )

                next_cursor = data.get("next_cursor")
                if not (data.get("has_next_page") and next_cursor):
                    break

        tweets.sort(
            key=lambda item: self._parse_tweet_datetime(item)
            or datetime.min.replace(tzinfo=timezone.utc)
        )
        return tweets

    def _store_tweets_history(self, tweets: list[dict]) -> None:
        for tweet in tweets:
            try:
                record = self.history_store.add_tweet(
                    tweet,
                    account=str(self.target_account or ""),
                )
                logger.info(f"历史推文已记录为 #{record.short_id}")
            except Exception as error:
                tweet_id = self._extract_tweet_id(tweet) or "unknown"
                logger.error(f"历史推文入库失败 tweet_id={tweet_id}: {error}")

    @staticmethod
    def _normalize_history_short_id(raw_value: str | None) -> str | None:
        short_id = TweetHistoryStore.normalize_short_id(raw_value)
        return short_id if len(short_id) == 6 else None

    @staticmethod
    def _parse_x_command(message: str) -> tuple[str | None, str | None]:
        raw_message = str(message or "").strip()
        match = re.match(
            r"^/?x(?:\s+(?P<short_id>\S+)(?P<translation>[\s\S]*))?\s*$",
            raw_message,
            flags=re.IGNORECASE,
        )
        if not match:
            return None, None

        short_id = TweetHistoryStore.normalize_short_id(match.group("short_id"))
        if len(short_id) != 6:
            short_id = None
        translation = match.group("translation")
        if translation is not None:
            translation = translation.lstrip()
            if not translation.strip():
                translation = None
        return short_id, translation

    @staticmethod
    def _summarize_history_text(text: str, limit: int = 80) -> str:
        summary = " ".join(str(text or "").split()) or "(无正文)"
        if len(summary) <= limit:
            return summary
        return f"{summary[: limit - 1]}..."

    def _build_history_list_message(self, limit: int = 10) -> str:
        records = self.history_store.list_recent(limit)
        if not records:
            return "暂无历史推文。"

        lines = [f"最近 {len(records)} 条历史推文："]
        for index, record in enumerate(records, 1):
            created_at = record.created_at or record.stored_at
            lines.append(
                f"{index}. #{record.short_id} · {created_at}\n"
                f"{self._summarize_history_text(record.original_text)}"
            )
        return "\n".join(lines)

    async def _render_history_record_to_base64(
        self,
        record,
        translation_text: str | None,
    ) -> str:
        extra_options = None
        if translation_text is not None:
            extra_options = {
                "text_override": translation_text,
                "translation_style": True,
            }
        avatar_account = await self._ensure_history_record_avatar_cached(record)
        return await self._render_tweet_to_base64(
            record.tweet,
            extra_options,
            avatar_account=avatar_account,
        )

    def _history_record_account(self, record) -> str | None:
        account = getattr(record, "account", None) or self._target_account_name()
        account = str(account or "").strip().lstrip("@")
        return account or None

    async def _ensure_history_record_avatar_cached(self, record) -> str | None:
        account = self._history_record_account(record)
        if not account:
            return None
        if self._cached_avatar_image_source(account) is not None:
            return account
        async with httpx.AsyncClient(timeout=30) as client:
            await self._ensure_target_avatar_cached(client, account)
        return account

    @filter.command("new", alias={"newx"})
    async def get_latest_tweet_command(self, event: AstrMessageEvent):
        """手动触发获取最新推文的命令。"""
        try:
            data = await self._fetch_new_tweets()
            if not data:
                yield event.plain_result(
                    f"{self.target_account} 在最近 {self.check_interval_minutes} 分钟内没有新推文。"
                )
                return

            self._store_tweets_history(data)
            lines = [f"找到 {len(data)} 条推文："]
            for index, tweet in enumerate(data):
                if index > 0:
                    lines.append("")
                lines.extend(self._build_tweet_display_lines(tweet, include_link=False))
            yield event.plain_result("\n".join(lines))
        except Exception as e:
            yield event.plain_result(f"请求失败: {e}")

    @filter.command("history")
    async def get_history_command(self, event: AstrMessageEvent):
        """查看最近保存的历史推文。"""
        try:
            yield event.plain_result(self._build_history_list_message(limit=10))
        except Exception as e:
            yield event.plain_result(f"读取历史推文失败: {e}")

    @filter.command("x")
    async def render_history_tweet_command(self, event: AstrMessageEvent):
        """按短 ID 重新渲染历史推文，或用翻译正文渲染翻译版。"""
        try:
            short_id, translation_text = self._parse_x_command(event.get_message_str())
            if short_id is None:
                yield event.plain_result("用法：/x <短ID> [翻译正文]")
                return

            try:
                record = self.history_store.get_by_short_id(short_id)
            except TweetHistoryLookupCollision as error:
                yield event.plain_result(str(error))
                return

            if record is None:
                yield event.plain_result(f"未找到 #{short_id} 对应的历史推文。")
                return

            image_base64 = await self._render_history_record_to_base64(
                record,
                translation_text,
            )
            yield event.make_result().base64_image(image_base64)
        except Exception as e:
            yield event.plain_result(f"渲染历史推文失败: {e}")

    async def check_for_new_tweets(self) -> None:
        """用于获取最新推文并记录的计划任务。"""
        try:
            data = await self._fetch_new_tweets()
            if not data:
                logger.info(
                    f"@{self.target_account} 在最近 {self.check_interval_minutes} 分钟内没有新推文。"
                )
                return

            logger.info(f"发现 {len(data)} 条来自 @{self.target_account} 的新推文。")
            self._store_tweets_history(data)
            try:
                await self.notify_subscribers(data)
            except Exception as notify_error:
                logger.error(
                    f"计划任务 'check_for_new_tweets' 通知阶段失败，"
                    f"已保留历史推文记录: {notify_error}"
                )
            for tweet in data:
                created_at = self._format_created_at(tweet)
                text = self._sanitize_tweet_text(tweet)
                logger.info(f"[{created_at}] {text}")
        except Exception as e:
            logger.error(f"计划任务 'check_for_new_tweets' 失败: {e}")

    async def terminate(self):
        """通过取消计划任务来清理插件。"""
        font_task = getattr(self, "_font_bootstrap_task", None)
        if font_task is not None and not font_task.done():
            font_task.cancel()
        if self.job_id:
            self.scheduler.cancel_job(self.job_id)
            logger.info(f"成功取消了任务 'check_for_new_tweets'，ID为: {self.job_id}")
