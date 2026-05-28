from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from history_store import TweetHistoryLookupCollision, TweetHistoryStore


def _tweet(tweet_id: str, text: str) -> dict:
    return {
        "id": tweet_id,
        "text": text,
        "createdAt": "Wed May 01 01:02:00 +0000 2024",
        "author": {"userName": "Blue_ArchiveJP"},
    }


class TweetHistoryStoreTest(unittest.TestCase):
    def _store(self, tmp_dir: str) -> TweetHistoryStore:
        return TweetHistoryStore(Path(tmp_dir) / "tweet-history.sqlite3")

    def test_add_tweet_hashes_original_text_and_round_trips_raw_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = self._store(tmp_dir)
            tweet = _tweet("123", "原始正文 😀\n#测试")

            record = store.add_tweet(tweet, account="@Blue_ArchiveJP")

            expected_hash = hashlib.sha256(tweet["text"].encode("utf-8")).hexdigest()
            self.assertEqual(record.full_hash, expected_hash)
            self.assertEqual(record.short_id, expected_hash[:6])
            self.assertEqual(record.original_text, "原始正文 😀\n#测试")
            self.assertEqual(record.tweet, tweet)
            self.assertEqual(record.link, "https://x.com/Blue_ArchiveJP/status/123")

    def test_duplicate_original_text_is_not_inserted_twice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = self._store(tmp_dir)

            first = store.add_tweet(_tweet("1", "same text"), account="Blue_ArchiveJP")
            second = store.add_tweet(_tweet("2", "same text"), account="Blue_ArchiveJP")

            self.assertEqual(first.row_id, second.row_id)
            self.assertEqual(len(store.list_recent(10)), 1)
            self.assertEqual(store.list_recent(10)[0].tweet_id, "1")

    def test_list_recent_is_newest_first_and_limited(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = self._store(tmp_dir)
            for index in range(12):
                store.add_tweet(_tweet(str(index), f"text {index}"))

            records = store.list_recent(10)

            self.assertEqual(len(records), 10)
            self.assertEqual([record.original_text for record in records[:3]], [
                "text 11",
                "text 10",
                "text 9",
            ])

    def test_get_by_short_id_is_case_insensitive_and_accepts_hash_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = self._store(tmp_dir)
            record = store.add_tweet(_tweet("1", "lookup text"))

            self.assertEqual(
                store.get_by_short_id(f"#{record.short_id.upper()}"),
                record,
            )
            self.assertIsNone(store.get_by_short_id("#missing"))

    def test_get_by_short_id_reports_collisions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = self._store(tmp_dir)
            first = store.add_tweet(_tweet("1", "first"))
            second = store.add_tweet(_tweet("2", "second"))

            with store._connect() as connection:
                connection.execute(
                    "UPDATE tweet_history SET short_id = ? WHERE id = ?",
                    (first.short_id, second.row_id),
                )

            with self.assertRaises(TweetHistoryLookupCollision):
                store.get_by_short_id(first.short_id)


if __name__ == "__main__":
    unittest.main()
