from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, MutableMapping

try:
    from .tweet_translator import DEFAULT_TRANSLATION_SYSTEM_PROMPT
except ImportError:  # pragma: no cover - direct test import fallback.
    from tweet_translator import DEFAULT_TRANSLATION_SYSTEM_PROMPT


ConfigMapping = MutableMapping[str, Any]
GROUP_RENDER_OVERRIDE_TEMPLATE_KEY = "group"


@dataclass(frozen=True)
class RenderProfile:
    user_name: str | None = None
    display_name: str | None = None
    username: str | None = None
    avatar: str | None = None

    def to_config(self) -> dict[str, str]:
        profile: dict[str, str] = {}
        if self.user_name:
            profile["user_name"] = self.user_name
        if self.display_name:
            profile["display_name"] = self.display_name
        if self.username:
            profile["username"] = self.username
        if self.avatar:
            profile["avatar"] = self.avatar
        return profile

    def to_render_options(self) -> dict[str, str]:
        options: dict[str, str] = {}
        if self.avatar:
            options["avatar"] = self.avatar
        if self.display_name:
            options["display_name"] = self.display_name
        if self.username:
            options["username"] = self.username
        return options


@dataclass(frozen=True)
class XMonitorRuntimeConfig:
    api_key: str
    target_account: str | None
    check_interval_minutes: int
    subscribe_groups: list[str]
    notify_user: str | None
    source_logo: str | None
    auto_translation_enabled: bool
    translation_provider_id: str | None
    translation_system_prompt: str
    group_render_overrides: dict[str, RenderProfile]

    def render_profile_for_group(self, group_id: str | None) -> RenderProfile | None:
        normalized = normalize_optional_text(group_id)
        if normalized is None:
            return None
        return self.group_render_overrides.get(normalized)

    def render_options_for_group(self, group_id: str | None) -> dict[str, str] | None:
        profile = self.render_profile_for_group(group_id)
        if profile is None:
            return None
        options = profile.to_render_options()
        return options or None


def normalize_optional_text(raw_value: Any) -> str | None:
    if raw_value is None:
        return None
    text = str(raw_value).strip()
    return text or None


def normalize_bool(raw_value: Any) -> bool:
    if isinstance(raw_value, bool):
        return raw_value
    text = normalize_optional_text(raw_value)
    if text is None:
        return False
    return text.lower() in {"1", "true", "yes", "on", "enable", "enabled", "开", "开启"}


def normalize_subscribe_groups(raw_value: Any) -> list[str]:
    if raw_value is None:
        return []
    if isinstance(raw_value, str):
        parts: list[Any] = raw_value.replace("\n", ",").split(",")
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


def normalize_notify_user(raw_value: Any) -> str | None:
    return normalize_optional_text(raw_value)


def _resolve_plugin_path(raw_value: Any, base_dir: Path) -> str | None:
    text = normalize_optional_text(raw_value)
    if text is None:
        return None
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return str(path)


def normalize_source_logo(raw_value: Any, base_dir: Path) -> str | None:
    return _resolve_plugin_path(raw_value, base_dir)


def normalize_avatar_source(raw_value: Any, base_dir: Path) -> str | None:
    avatar = normalize_optional_text(raw_value)
    if avatar is None:
        return None
    if avatar.startswith(("http://", "https://")):
        return avatar
    return _resolve_plugin_path(avatar, base_dir)


def normalize_render_profile(raw_value: Any, base_dir: Path) -> RenderProfile:
    if not isinstance(raw_value, dict):
        return RenderProfile()

    user_name = normalize_optional_text(
        raw_value.get("user_name") or raw_value.get("userName")
    )
    display_name = normalize_optional_text(
        raw_value.get("display_name")
        or raw_value.get("displayName")
        or raw_value.get("name")
    )
    username = normalize_optional_text(
        raw_value.get("username")
        or raw_value.get("screen_name")
        or raw_value.get("screenName")
    )
    avatar = normalize_avatar_source(
        raw_value.get("avatar")
        or raw_value.get("profilePicture")
        or raw_value.get("profile_picture"),
        base_dir,
    )
    return RenderProfile(
        user_name=user_name.lstrip("@") if user_name else None,
        display_name=display_name,
        username=username.lstrip("@") if username else None,
        avatar=avatar,
    )


def normalize_group_render_overrides(
    raw_value: Any,
    base_dir: Path,
) -> dict[str, RenderProfile]:
    overrides: dict[str, RenderProfile] = {}
    if isinstance(raw_value, dict):
        entries = list(raw_value.items())
    elif isinstance(raw_value, list):
        entries = [
            (
                item.get("group_id") or item.get("groupId") or item.get("group"),
                item,
            )
            for item in raw_value
            if isinstance(item, dict)
        ]
    else:
        return overrides

    for group_id, profile in entries:
        normalized_group_id = normalize_optional_text(group_id)
        if normalized_group_id is None:
            continue
        normalized_profile = normalize_render_profile(profile, base_dir)
        if normalized_profile.to_config():
            overrides[normalized_group_id] = normalized_profile
    return overrides


def serialize_group_render_overrides(
    overrides: dict[str, RenderProfile],
) -> list[dict[str, str]]:
    serialized: list[dict[str, str]] = []
    for group_id, profile in overrides.items():
        profile_config = profile.to_config()
        if not profile_config:
            continue
        serialized.append(
            {
                "__template_key": GROUP_RENDER_OVERRIDE_TEMPLATE_KEY,
                "group_id": group_id,
                **profile_config,
            }
        )
    return serialized


def validate_check_interval_minutes(raw_value: Any) -> int:
    interval_minutes = int(raw_value)
    if interval_minutes <= 0:
        raise ValueError("CHECK_INTERVAL 必须大于 0，单位为分钟")
    return interval_minutes


def normalize_check_interval_minutes(raw_value: Any) -> tuple[int, bool]:
    return validate_check_interval_minutes(raw_value), False


def save_config(config: ConfigMapping) -> None:
    save = getattr(config, "save_config", None)
    if callable(save):
        save()


def load_runtime_config(
    config: ConfigMapping,
    *,
    base_dir: Path,
    logger: Any | None = None,
) -> XMonitorRuntimeConfig:
    interval_minutes, migrated = normalize_check_interval_minutes(
        config.get("CHECK_INTERVAL")
    )
    if migrated:
        config["CHECK_INTERVAL"] = interval_minutes
        save_config(config)
        if logger is not None:
            logger.info(
                f"检测到旧版秒级 CHECK_INTERVAL，已迁移为 {interval_minutes} 分钟。"
            )

    translation_prompt = (
        normalize_optional_text(
            config.get("TRANSLATION_SYSTEM_PROMPT", DEFAULT_TRANSLATION_SYSTEM_PROMPT)
        )
        or DEFAULT_TRANSLATION_SYSTEM_PROMPT
    )
    return XMonitorRuntimeConfig(
        api_key=str(config.get("X-API", "") or ""),
        target_account=normalize_optional_text(config.get("TARGET_ACCOUNT")),
        check_interval_minutes=interval_minutes,
        subscribe_groups=normalize_subscribe_groups(config.get("SUBSCRIBE_GROUPS", [])),
        notify_user=normalize_notify_user(config.get("NOTIFY_USER", "")),
        source_logo=normalize_source_logo(
            config.get("SOURCE_LOGO", "assets/source_logo.png"),
            base_dir,
        ),
        auto_translation_enabled=normalize_bool(
            config.get("ENABLE_AUTO_TRANSLATION", False)
        ),
        translation_provider_id=normalize_optional_text(
            config.get("TRANSLATION_PROVIDER_ID", "")
        ),
        translation_system_prompt=translation_prompt,
        group_render_overrides=normalize_group_render_overrides(
            config.get("GROUP_RENDER_OVERRIDES", {}),
            base_dir,
        ),
    )


def set_auto_translation_enabled(
    config: ConfigMapping,
    enabled: bool,
) -> None:
    config["ENABLE_AUTO_TRANSLATION"] = bool(enabled)
    save_config(config)


def set_group_render_profile(
    config: ConfigMapping,
    group_id: str,
    profile_update: dict[str, Any],
    *,
    base_dir: Path,
) -> RenderProfile:
    normalized_group_id = normalize_optional_text(group_id)
    if normalized_group_id is None:
        raise ValueError("缺少 group_id")

    raw_overrides = normalize_group_render_overrides(
        config.get("GROUP_RENDER_OVERRIDES", {}),
        base_dir,
    )
    existing = raw_overrides.get(normalized_group_id)
    merged_profile = existing.to_config() if existing is not None else {}
    merged_profile.update(profile_update)
    normalized_profile = normalize_render_profile(merged_profile, base_dir)

    raw_overrides[normalized_group_id] = normalized_profile
    config["GROUP_RENDER_OVERRIDES"] = serialize_group_render_overrides(raw_overrides)
    save_config(config)
    return normalized_profile


def build_config_status_message(
    runtime_config: XMonitorRuntimeConfig,
    current_group_id: str | None = None,
) -> str:
    translation_state = "开启" if runtime_config.auto_translation_enabled else "关闭"
    provider = runtime_config.translation_provider_id or "未配置"
    lines = [
        "xmonitor 当前配置：",
        f"- 自动翻译：{translation_state}",
        f"- 翻译 Provider：{provider}",
        f"- 群身份覆盖：{len(runtime_config.group_render_overrides)} 个群",
    ]

    if current_group_id:
        current_profile = runtime_config.render_profile_for_group(current_group_id)
        if current_profile is not None:
            display_name = current_profile.display_name or "未覆盖"
            username = current_profile.username or "未覆盖"
            avatar = "已配置" if current_profile.avatar else "未配置"
            lines.append(
                f"- 当前群 {current_group_id}："
                f"{display_name} / @{username} / 头像{avatar}"
            )
        else:
            lines.append(f"- 当前群 {current_group_id}：未配置身份覆盖")
    return "\n".join(lines)
