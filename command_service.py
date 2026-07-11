from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, MutableMapping

try:
    from .config import (
        XMonitorRuntimeConfig,
        build_config_status_message,
        set_auto_translation_enabled,
        set_group_render_profile,
    )
    from .history_store import TweetHistoryLookupCollision, TweetHistoryStore
    from .history_service import HistoryService
    from .message_builder import MessageBuilder
    from .render_service import RenderService
    from .twitter_client import TwitterClient
except ImportError:  # pragma: no cover - direct test import fallback.
    from config import (
        XMonitorRuntimeConfig,
        build_config_status_message,
        set_auto_translation_enabled,
        set_group_render_profile,
    )
    from history_store import TweetHistoryLookupCollision, TweetHistoryStore
    from history_service import HistoryService
    from message_builder import MessageBuilder
    from render_service import RenderService
    from twitter_client import TwitterClient


ConfigMapping = MutableMapping[str, Any]


@dataclass(frozen=True)
class CommandRenderResult:
    text: str | None = None
    image_base64: str | None = None

    @property
    def is_image(self) -> bool:
        return self.image_base64 is not None


def parse_x_command(message: str) -> tuple[str | None, str | None]:
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


def parse_xmonitor_args(message: str) -> list[str]:
    raw_message = str(message or "").strip()
    raw_message = re.sub(
        r"^/?xmonitor\b",
        "",
        raw_message,
        flags=re.IGNORECASE,
    ).strip()
    return raw_message.split()


def event_group_id(event: Any) -> str | None:
    get_group_id = getattr(event, "get_group_id", None)
    if callable(get_group_id):
        group_id = str(get_group_id()).strip()
        return group_id or None

    message_obj = getattr(event, "message_obj", None)
    message_group_id = getattr(message_obj, "group_id", None)
    if message_group_id is None:
        return None
    group_id_text = str(message_group_id).strip()
    return group_id_text or None


def xmonitor_usage() -> str:
    return (
        "用法：\n"
        "/xmonitor config status\n"
        "/xmonitor config translation on|off\n"
        "/xmonitor config avatar <group_id> <userName>\n"
        "/xmonitor config avatar current <userName>\n"
        "/xmonitor config identity <group_id> <display_name> <username>"
    )


class CommandService:
    def __init__(
        self,
        *,
        runtime_config: XMonitorRuntimeConfig,
        config: ConfigMapping,
        base_dir: Path,
        twitter_client: TwitterClient,
        history_service: HistoryService,
        render_service: RenderService,
        message_builder: MessageBuilder,
        on_config_changed: Callable[[], XMonitorRuntimeConfig] | None = None,
    ) -> None:
        self.runtime_config = runtime_config
        self.config = config
        self.base_dir = base_dir
        self.twitter_client = twitter_client
        self.history_service = history_service
        self.render_service = render_service
        self.message_builder = message_builder
        self.on_config_changed = on_config_changed

    async def handle_manual_fetch(self) -> str:
        data = await self.twitter_client.fetch_recent_tweets()
        if not data:
            return (
                f"{self.runtime_config.target_account} 在最近 "
                f"{self.runtime_config.check_interval_minutes} 分钟内没有新推文。"
            )

        self.history_service.store_new_tweets(data)
        return self.message_builder.build_manual_fetch_message(data)

    def handle_history(self, limit: int = 10) -> str:
        return self.history_service.build_history_list_message(limit)

    async def handle_render_history(self, message: str) -> CommandRenderResult:
        short_id, translation_text = parse_x_command(message)
        if short_id is None:
            return CommandRenderResult(text="用法：/x <短ID> [翻译正文]")

        try:
            record = self.history_service.get_by_short_id(short_id)
        except TweetHistoryLookupCollision as error:
            return CommandRenderResult(text=str(error))

        if record is None:
            return CommandRenderResult(text=f"未找到 #{short_id} 对应的历史推文。")

        image_base64 = await self.render_service.render_history_record_to_base64(
            record,
            translation_text,
        )
        return CommandRenderResult(image_base64=image_base64)

    async def handle_xmonitor_config(self, event: Any, args: list[str]) -> str:
        if len(args) < 2 or args[0].lower() != "config":
            return xmonitor_usage()

        action = args[1].lower()
        if action == "status":
            return build_config_status_message(
                self.runtime_config,
                event_group_id(event),
            )

        if action == "translation":
            if len(args) != 3 or args[2].lower() not in {"on", "off"}:
                return "用法：/xmonitor config translation on|off"
            enabled = args[2].lower() == "on"
            set_auto_translation_enabled(self.config, enabled)
            self._refresh_runtime()
            return f"定时推送自动翻译已{'开启' if enabled else '关闭'}。"

        if action == "avatar":
            if len(args) != 4:
                return "用法：/xmonitor config avatar <group_id|current> <userName>"
            if args[2].lower() == "current":
                group_id = event_group_id(event)
                if group_id is None:
                    return "当前会话不是群聊，请显式传入 group_id。"
            else:
                group_id = args[2]
            profile = await self.set_group_avatar_from_user_profile(group_id, args[3])
            return (
                f"已为群 {group_id} 配置头像："
                f"{profile.get('display_name', args[3])} / "
                f"@{profile.get('username', args[3])}"
            )

        if action == "identity":
            if len(args) != 5:
                return "用法：/xmonitor config identity <group_id> <display_name> <username>"
            group_id, display_name, username = args[2], args[3], args[4]
            profile = set_group_render_profile(
                self.config,
                group_id,
                {
                    "display_name": display_name,
                    "username": username,
                },
                base_dir=self.base_dir,
            ).to_config()
            self._refresh_runtime()
            return (
                f"已为群 {group_id} 配置显示身份："
                f"{profile.get('display_name', display_name)} / "
                f"@{profile.get('username', username.lstrip('@'))}"
            )

        return xmonitor_usage()

    async def set_group_avatar_from_user_profile(
        self,
        group_id: str,
        user_name: str,
    ) -> dict[str, str]:
        profile = await self.twitter_client.fetch_user_profile(user_name)
        normalized = set_group_render_profile(
            self.config,
            group_id,
            {
                "user_name": profile.get("userName") or user_name,
                "display_name": profile.get("name") or profile.get("userName"),
                "username": profile.get("userName") or user_name,
                "avatar": profile.get("profilePicture"),
            },
            base_dir=self.base_dir,
        ).to_config()
        self._refresh_runtime()
        return normalized

    def _refresh_runtime(self) -> None:
        if self.on_config_changed is not None:
            self.runtime_config = self.on_config_changed()
