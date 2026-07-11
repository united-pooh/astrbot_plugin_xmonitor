from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

try:
    from .history_store import TweetHistoryStore
except ImportError:  # pragma: no cover - direct test import fallback.
    from history_store import TweetHistoryStore


def build_search_query(target_account: str | None, check_interval_minutes: int) -> str:
    account = str(target_account or "").strip().lstrip("@")
    if not account:
        raise RuntimeError("缺少 TARGET_ACCOUNT")
    if int(check_interval_minutes) <= 0:
        raise ValueError("CHECK_INTERVAL 必须大于 0，单位为分钟")
    return (
        f"from:{account} "
        "include:nativeretweets "
        f"within_time:{int(check_interval_minutes)}m"
    )


def extract_tweet_id(tweet: dict[str, Any]) -> str | None:
    tweet_id = (
        tweet.get("id")
        or tweet.get("tweet_id")
        or tweet.get("rest_id")
        or tweet.get("tweetId")
    )
    if tweet_id is None:
        return None
    return str(tweet_id)


def sanitize_tweet_text(tweet: dict[str, Any]) -> str:
    text = str(tweet.get("text", "")).replace("\n", " ").strip()
    return " ".join(text.split()) or "(无正文)"


def tweet_body_text(tweet: dict[str, Any]) -> str:
    return str(tweet.get("text") or "").strip() or "(无正文)"


def history_short_id_for_tweet(tweet: dict[str, Any]) -> str:
    return TweetHistoryStore.hash_text(str(tweet.get("text") or ""))[1]


def _is_remote_image_url(value: Any) -> bool:
    return str(value or "").strip().lower().startswith(("http://", "https://"))


def extract_tweet_image_urls(tweet: dict[str, Any]) -> list[str]:
    urls: list[str] = []

    def add_url(value: Any) -> None:
        url = str(value or "").strip()
        if url and _is_remote_image_url(url) and url not in urls:
            urls.append(url)

    def add_media_entry(media: Any) -> None:
        if isinstance(media, dict):
            preferred_keys = (
                "media_url_https",
                "media_url",
                "preview_image_url",
                "image_url",
                "imageUrl",
            )
            for key in preferred_keys:
                add_url(media.get(key))
            if not any(media.get(key) for key in preferred_keys):
                add_url(media.get("url"))
        else:
            add_url(media)

    for container_name in ("extendedEntities", "extended_entities", "entities"):
        container = tweet.get(container_name) or {}
        if isinstance(container, dict):
            for media in container.get("media") or []:
                add_media_entry(media)

    for media in tweet.get("media") or tweet.get("images") or []:
        add_media_entry(media)

    return urls


def parse_tweet_datetime(tweet: dict[str, Any]) -> datetime | None:
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


def format_created_at(tweet: dict[str, Any]) -> str:
    parsed = parse_tweet_datetime(tweet)
    if parsed is None:
        return str(tweet.get("createdAt", "unknown time"))
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def build_tweet_display_lines(
    tweet: dict[str, Any],
    *,
    target_account: str | None,
    include_link: bool,
) -> list[str]:
    lines = [
        format_created_at(tweet),
        sanitize_tweet_text(tweet),
    ]
    tweet_id = extract_tweet_id(tweet)
    if include_link and tweet_id:
        account = str(target_account or "").strip().lstrip("@")
        lines.append(f"https://x.com/{account}/status/{tweet_id}")
    return lines


def summarize_history_text(text: str, limit: int = 80) -> str:
    summary = " ".join(str(text or "").split()) or "(无正文)"
    if len(summary) <= limit:
        return summary
    return f"{summary[: limit - 1]}..."


def normalize_history_short_id(raw_value: str | None) -> str | None:
    short_id = TweetHistoryStore.normalize_short_id(raw_value)
    return short_id if len(short_id) == 6 else None
