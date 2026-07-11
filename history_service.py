from __future__ import annotations

from dataclasses import dataclass
from typing import Any

try:
    from .history_store import TweetHistoryRecord, TweetHistoryStore
    from .tweet_helpers import extract_tweet_id, summarize_history_text
except ImportError:  # pragma: no cover - direct test import fallback.
    from history_store import TweetHistoryRecord, TweetHistoryStore
    from tweet_helpers import extract_tweet_id, summarize_history_text


@dataclass(frozen=True)
class StoredTweet:
    tweet: dict[str, Any]
    record: Any


class HistoryService:
    def __init__(
        self,
        history_store: TweetHistoryStore,
        *,
        account: str | None,
        logger: Any | None = None,
    ) -> None:
        self.history_store = history_store
        self.account = account
        self.logger = logger

    def store_new_tweets(self, tweets: list[dict[str, Any]]) -> list[StoredTweet]:
        stored_tweets: list[StoredTweet] = []
        for tweet in tweets:
            try:
                original_text = str(tweet.get("text") or "")
                full_hash, short_id = TweetHistoryStore.hash_text(original_text)
                if self.history_store.get_by_full_hash(full_hash) is not None:
                    self._info(f"历史推文已存在，跳过重复通知 #{short_id}")
                    continue

                record = self.history_store.add_tweet(tweet, account=self.account)
                stored_tweets.append(StoredTweet(tweet=tweet, record=record))
                self._info(f"历史推文已记录为 #{record.short_id}")
            except Exception as error:
                tweet_id = extract_tweet_id(tweet) or "unknown"
                self._error(f"历史推文入库失败 tweet_id={tweet_id}: {error}")
        return stored_tweets

    def list_recent(self, limit: int = 10) -> list[TweetHistoryRecord]:
        return self.history_store.list_recent(limit)

    def get_by_short_id(self, short_id: str) -> TweetHistoryRecord | None:
        return self.history_store.get_by_short_id(short_id)

    def build_history_list_message(self, limit: int = 10) -> str:
        records = self.history_store.list_recent(limit)
        if not records:
            return "暂无历史推文。"

        lines = [f"最近 {len(records)} 条历史推文："]
        for index, record in enumerate(records, 1):
            created_at = record.created_at or record.stored_at
            lines.append(
                f"{index}. #{record.short_id} · {created_at}\n"
                f"{summarize_history_text(record.original_text)}"
            )
        return "\n".join(lines)

    def _info(self, message: str) -> None:
        if self.logger is not None:
            self.logger.info(message)

    def _error(self, message: str) -> None:
        if self.logger is not None:
            self.logger.error(message)
