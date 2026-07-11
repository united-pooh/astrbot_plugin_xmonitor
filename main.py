from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register

try:
    from .command_service import CommandService, parse_xmonitor_args
    from .config import XMonitorRuntimeConfig, load_runtime_config
    from .history_service import HistoryService, StoredTweet
    from .history_store import TweetHistoryStore
    from .message_builder import MessageBuilder
    from .notification_plan import NotificationBatchDebug
    from .notification_sender import NotificationSender
    from .render_service import RenderService
    from .scheduler import SchedulerManager
    from .translation_service import TranslationService
    from .tweet_helpers import (
        format_created_at,
        history_short_id_for_tweet,
        sanitize_tweet_text,
    )
    from .twitter_client import TwitterClient
except ImportError:  # pragma: no cover - local script fallback for lightweight probes.
    from command_service import CommandService, parse_xmonitor_args
    from config import XMonitorRuntimeConfig, load_runtime_config
    from history_service import HistoryService, StoredTweet
    from history_store import TweetHistoryStore
    from message_builder import MessageBuilder
    from notification_plan import NotificationBatchDebug
    from notification_sender import NotificationSender
    from render_service import RenderService
    from scheduler import SchedulerManager
    from translation_service import TranslationService
    from tweet_helpers import (
        format_created_at,
        history_short_id_for_tweet,
        sanitize_tweet_text,
    )
    from twitter_client import TwitterClient


@register("astrbot_plugin_xmonitor", "united_pooh", "一个api调用器", "1.2.0")
class XMonitor(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.scheduler = SchedulerManager()
        self.config = config
        self.job_id: str | None = None
        self.history_store = TweetHistoryStore(self._history_db_path())
        self.runtime_config = self._reload_runtime()

    @staticmethod
    def _plugin_dir() -> Path:
        return Path(__file__).resolve().parent

    @classmethod
    def _history_db_path(cls) -> Path:
        return cls._plugin_dir() / "data" / "tweet_history.sqlite3"

    def _reload_runtime(self) -> XMonitorRuntimeConfig:
        runtime_config = load_runtime_config(
            self.config,
            base_dir=self._plugin_dir(),
            logger=logger,
        )
        self.runtime_config = runtime_config
        self._apply_runtime_attrs(runtime_config)
        self._build_services(runtime_config)
        return runtime_config

    def _apply_runtime_attrs(self, runtime_config: XMonitorRuntimeConfig) -> None:
        self.api_key = runtime_config.api_key
        self.target_account = runtime_config.target_account
        self.check_interval_minutes = runtime_config.check_interval_minutes
        self.subscribe_groups = runtime_config.subscribe_groups
        self.notify_user = runtime_config.notify_user
        self.source_logo = runtime_config.source_logo
        self.auto_translation_enabled = runtime_config.auto_translation_enabled
        self.translation_provider_id = runtime_config.translation_provider_id
        self.translation_system_prompt = runtime_config.translation_system_prompt
        self.group_render_overrides = runtime_config.group_render_overrides

    def _build_services(self, runtime_config: XMonitorRuntimeConfig) -> None:
        self.twitter_client = TwitterClient(
            api_key=runtime_config.api_key,
            target_account=runtime_config.target_account,
            check_interval_minutes=runtime_config.check_interval_minutes,
        )
        self.history_service = HistoryService(
            self.history_store,
            account=runtime_config.target_account,
            logger=logger,
        )
        self.render_service = RenderService(source_logo=runtime_config.source_logo)
        self.translation_service = TranslationService(
            context=self.context,
            enabled=runtime_config.auto_translation_enabled,
            provider_id=runtime_config.translation_provider_id,
            system_prompt=runtime_config.translation_system_prompt,
            logger=logger,
        )
        self.message_builder = MessageBuilder(
            target_account=runtime_config.target_account,
            notify_user=runtime_config.notify_user,
        )
        self.notification_sender = NotificationSender(
            subscribe_groups=runtime_config.subscribe_groups,
            target_account=runtime_config.target_account,
            render_service=self.render_service,
            translation_service=self.translation_service,
            message_builder=self.message_builder,
            send_message=StarTools.send_message_by_id,
            render_options_for_group=runtime_config.render_options_for_group,
            logger=logger,
        )
        self.command_service = CommandService(
            runtime_config=runtime_config,
            config=self.config,
            base_dir=self._plugin_dir(),
            twitter_client=self.twitter_client,
            history_service=self.history_service,
            render_service=self.render_service,
            message_builder=self.message_builder,
            on_config_changed=self._reload_runtime,
        )

    async def initialize(self) -> None:
        """初始化插件并按分钟间隔调度 Twitter 轮询任务。"""
        self._reload_runtime()
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
                f"账号: @{self.target_account}，将在插件启动后每 "
                f"{self.check_interval_minutes} 分钟执行一次"
            )
            if self.subscribe_groups:
                logger.info(
                    f"定时通知已启用，将向 {len(self.subscribe_groups)} 个群广播更新。"
                )
            else:
                logger.warning(
                    "SUBSCRIBE_GROUPS 为空，定时任务发现新推文时不会主动推送。"
                )

    async def _fetch_new_tweets(self) -> list[dict[str, Any]]:
        return await self.twitter_client.fetch_recent_tweets()

    def _store_tweets_history(
        self, tweets: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        return [item.tweet for item in self.history_service.store_new_tweets(tweets)]

    async def notify_subscribers(
        self,
        tweets: list[StoredTweet] | list[dict[str, Any]],
    ) -> NotificationBatchDebug:
        return await self.notification_sender.notify(
            self._coerce_notification_items(tweets)
        )

    @staticmethod
    def _coerce_notification_items(
        tweets: list[StoredTweet] | list[dict[str, Any]],
    ) -> list[StoredTweet]:
        items: list[StoredTweet] = []
        for item in tweets:
            if isinstance(item, StoredTweet):
                items.append(item)
                continue
            record = SimpleNamespace(short_id=history_short_id_for_tweet(item))
            items.append(StoredTweet(tweet=item, record=record))
        return items

    @filter.command("xmonitor")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def xmonitor_config_command(self, event: AstrMessageEvent):
        """管理 xmonitor 运行时配置。"""
        try:
            result = await self.command_service.handle_xmonitor_config(
                event,
                parse_xmonitor_args(event.get_message_str()),
            )
            yield event.plain_result(result)
        except Exception as e:
            yield event.plain_result(f"配置修改失败: {e}")

    @filter.command("new", alias={"newx"})
    async def get_latest_tweet_command(self, event: AstrMessageEvent):
        """手动触发获取最新推文的命令。"""
        try:
            yield event.plain_result(await self.command_service.handle_manual_fetch())
        except Exception as e:
            yield event.plain_result(f"请求失败: {e}")

    @filter.command("history")
    async def get_history_command(self, event: AstrMessageEvent):
        """查看最近保存的历史推文。"""
        try:
            yield event.plain_result(self.command_service.handle_history(limit=10))
        except Exception as e:
            yield event.plain_result(f"读取历史推文失败: {e}")

    @filter.command("x")
    async def render_history_tweet_command(self, event: AstrMessageEvent):
        """按短 ID 重新渲染历史推文，或用翻译正文渲染翻译版。"""
        try:
            result = await self.command_service.handle_render_history(
                event.get_message_str()
            )
            if result.is_image:
                yield event.make_result().base64_image(result.image_base64)
                return
            yield event.plain_result(
                result.text or "渲染历史推文失败: 推文图片渲染结果为空"
            )
        except Exception as e:
            yield event.plain_result(f"渲染历史推文失败: {e}")

    async def check_for_new_tweets(self) -> None:
        """用于获取最新推文并记录、通知订阅群的计划任务。"""
        try:
            data = await self.twitter_client.fetch_recent_tweets()
            if not data:
                logger.info(
                    f"@{self.target_account} 在最近 "
                    f"{self.check_interval_minutes} 分钟内没有新推文。"
                )
                return

            logger.info(f"发现 {len(data)} 条来自 @{self.target_account} 的新推文。")
            stored_tweets = self.history_service.store_new_tweets(data)
            if not stored_tweets:
                logger.info("本次抓取的推文均已存在于历史库，跳过重复推送。")
                return

            batch_debug = await self.notification_sender.notify(stored_tweets)
            logger.info(
                "新推文通知完成："
                f"{len(batch_debug.results)} 条群组/推文结果，"
                f"skipped_reason={batch_debug.skipped_reason}"
            )
            for item in stored_tweets:
                created_at = format_created_at(item.tweet)
                text = sanitize_tweet_text(item.tweet)
                logger.info(f"[{created_at}] {text}")
        except Exception as e:
            logger.error(f"计划任务 'check_for_new_tweets' 失败: {e}")

    async def terminate(self) -> None:
        """通过取消计划任务来清理插件。"""
        if self.job_id:
            self.scheduler.cancel_job(self.job_id)
            logger.info(f"成功取消了任务 'check_for_new_tweets'，ID为: {self.job_id}")
