from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

try:
    from .history_service import StoredTweet
except ImportError:  # pragma: no cover - direct test import fallback.
    from history_service import StoredTweet


@dataclass(frozen=True)
class NotificationItem:
    group_id: str
    stored_tweet: StoredTweet


@dataclass(frozen=True)
class MessageSendDebug:
    kind: str
    status: str
    error: str | None = None


@dataclass(frozen=True)
class NotificationDebugResult:
    group_id: str
    short_id: str
    tweet_id: str | None
    translation_status: str
    render_status: str
    image_send: MessageSendDebug
    source_send: MessageSendDebug
    fallback_send: MessageSendDebug | None = None
    translation_error: str | None = None
    render_error: str | None = None


@dataclass(frozen=True)
class NotificationBatchDebug:
    results: list[NotificationDebugResult] = field(default_factory=list)
    skipped_reason: str | None = None

    @property
    def sent_count(self) -> int:
        return len(self.results)

    def as_dict(self) -> dict[str, Any]:
        return {
            "skipped_reason": self.skipped_reason,
            "results": [
                {
                    "group_id": result.group_id,
                    "short_id": result.short_id,
                    "tweet_id": result.tweet_id,
                    "translation_status": result.translation_status,
                    "render_status": result.render_status,
                    "image_send": result.image_send.__dict__,
                    "fallback_send": (
                        result.fallback_send.__dict__
                        if result.fallback_send is not None
                        else None
                    ),
                    "source_send": result.source_send.__dict__,
                    "translation_error": result.translation_error,
                    "render_error": result.render_error,
                }
                for result in self.results
            ],
        }


def make_notification_items(
    group_ids: list[str],
    stored_tweets: list[StoredTweet],
) -> list[NotificationItem]:
    return [
        NotificationItem(group_id=group_id, stored_tweet=stored_tweet)
        for group_id in group_ids
        for stored_tweet in stored_tweets
    ]
