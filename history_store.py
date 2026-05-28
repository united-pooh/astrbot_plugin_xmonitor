from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class TweetHistoryLookupCollision(RuntimeError):
    """Raised when one visible short id maps to multiple stored tweets."""


@dataclass(frozen=True)
class TweetHistoryRecord:
    row_id: int
    short_id: str
    full_hash: str
    original_text: str
    tweet: dict[str, Any]
    tweet_id: str | None
    account: str | None
    created_at: str | None
    stored_at: str

    @property
    def link(self) -> str | None:
        if not self.account or not self.tweet_id:
            return None
        account = self.account.strip().lstrip("@")
        if not account:
            return None
        return f"https://x.com/{account}/status/{self.tweet_id}"


@dataclass(frozen=True)
class UserAvatarRecord:
    account: str
    profile_picture_url: str
    avatar_base64: str
    stored_at: str


class TweetHistoryStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    @staticmethod
    def hash_text(text: str) -> tuple[str, str]:
        full_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return full_hash, full_hash[:6]

    def add_tweet(
        self,
        tweet: dict[str, Any],
        *,
        account: str | None = None,
    ) -> TweetHistoryRecord:
        original_text = self._original_text(tweet)
        full_hash, short_id = self.hash_text(original_text)
        stored_at = datetime.now(timezone.utc).isoformat()
        tweet_id = self._extract_tweet_id(tweet)
        created_at = self._string_or_none(tweet.get("createdAt"))
        tweet_json = json.dumps(tweet, ensure_ascii=False, sort_keys=True)

        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO tweet_history (
                    short_id,
                    full_hash,
                    original_text,
                    tweet_json,
                    tweet_id,
                    account,
                    created_at,
                    stored_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    short_id,
                    full_hash,
                    original_text,
                    tweet_json,
                    tweet_id,
                    self._normalize_account(account),
                    created_at,
                    stored_at,
                ),
            )
            row = connection.execute(
                """
                SELECT *
                FROM tweet_history
                WHERE full_hash = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (full_hash,),
            ).fetchone()

        if row is None:  # pragma: no cover - sqlite failure guard.
            raise RuntimeError("历史推文写入失败")
        return self._record_from_row(row)

    def add_tweets(
        self,
        tweets: list[dict[str, Any]],
        *,
        account: str | None = None,
    ) -> list[TweetHistoryRecord]:
        return [self.add_tweet(tweet, account=account) for tweet in tweets]

    def list_recent(self, limit: int = 10) -> list[TweetHistoryRecord]:
        limit = max(0, int(limit))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM tweet_history
                ORDER BY stored_at DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._record_from_row(row) for row in rows]

    def get_by_short_id(self, short_id: str) -> TweetHistoryRecord | None:
        normalized = self.normalize_short_id(short_id)
        if not normalized:
            return None
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM tweet_history
                WHERE lower(short_id) = ?
                ORDER BY stored_at DESC, id DESC
                """,
                (normalized,),
            ).fetchall()

        if not rows:
            return None
        full_hashes = {str(row["full_hash"]) for row in rows}
        if len(full_hashes) > 1:
            raise TweetHistoryLookupCollision(
                f"短 ID #{normalized} 命中 {len(rows)} 条历史推文"
            )
        return self._record_from_row(rows[0])

    def get_user_avatar(self, account: str | None) -> UserAvatarRecord | None:
        normalized = self._normalize_account(account)
        if normalized is None:
            return None
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM user_avatar_cache
                WHERE lower(account) = ?
                LIMIT 1
                """,
                (normalized.lower(),),
            ).fetchone()
        if row is None:
            return None
        return self._avatar_record_from_row(row)

    def save_user_avatar(
        self,
        account: str | None,
        *,
        profile_picture_url: str,
        avatar_base64: str,
    ) -> UserAvatarRecord:
        normalized = self._normalize_account(account)
        if normalized is None:
            raise ValueError("缺少用户账号，无法保存头像缓存")
        if not avatar_base64:
            raise ValueError("缺少头像 base64，无法保存头像缓存")

        stored_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO user_avatar_cache (
                    account,
                    profile_picture_url,
                    avatar_base64,
                    stored_at
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(account) DO UPDATE SET
                    profile_picture_url = excluded.profile_picture_url,
                    avatar_base64 = excluded.avatar_base64,
                    stored_at = excluded.stored_at
                """,
                (
                    normalized,
                    str(profile_picture_url),
                    str(avatar_base64),
                    stored_at,
                ),
            )
        record = self.get_user_avatar(normalized)
        if record is None:  # pragma: no cover - sqlite failure guard.
            raise RuntimeError("用户头像缓存写入失败")
        return record

    @staticmethod
    def normalize_short_id(value: str | None) -> str:
        if value is None:
            return ""
        return str(value).strip().lstrip("#").lower()[:6]

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tweet_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    short_id TEXT NOT NULL,
                    full_hash TEXT NOT NULL UNIQUE,
                    original_text TEXT NOT NULL,
                    tweet_json TEXT NOT NULL,
                    tweet_id TEXT,
                    account TEXT,
                    created_at TEXT,
                    stored_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tweet_history_short_id
                ON tweet_history(short_id)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tweet_history_stored_at
                ON tweet_history(stored_at)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS user_avatar_cache (
                    account TEXT PRIMARY KEY,
                    profile_picture_url TEXT NOT NULL,
                    avatar_base64 TEXT NOT NULL,
                    stored_at TEXT NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    @classmethod
    def _record_from_row(cls, row: sqlite3.Row) -> TweetHistoryRecord:
        return TweetHistoryRecord(
            row_id=int(row["id"]),
            short_id=str(row["short_id"]),
            full_hash=str(row["full_hash"]),
            original_text=str(row["original_text"]),
            tweet=json.loads(str(row["tweet_json"])),
            tweet_id=cls._string_or_none(row["tweet_id"]),
            account=cls._string_or_none(row["account"]),
            created_at=cls._string_or_none(row["created_at"]),
            stored_at=str(row["stored_at"]),
        )

    @staticmethod
    def _avatar_record_from_row(row: sqlite3.Row) -> UserAvatarRecord:
        return UserAvatarRecord(
            account=str(row["account"]),
            profile_picture_url=str(row["profile_picture_url"]),
            avatar_base64=str(row["avatar_base64"]),
            stored_at=str(row["stored_at"]),
        )

    @staticmethod
    def _original_text(tweet: dict[str, Any]) -> str:
        return str(tweet.get("text") or "")

    @staticmethod
    def _extract_tweet_id(tweet: dict[str, Any]) -> str | None:
        for key in ("id", "tweet_id", "rest_id", "tweetId"):
            value = tweet.get(key)
            if value is not None:
                return str(value)
        return None

    @staticmethod
    def _normalize_account(account: str | None) -> str | None:
        if account is None:
            return None
        normalized = str(account).strip().lstrip("@")
        return normalized or None

    @staticmethod
    def _string_or_none(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value)
        return text if text else None
