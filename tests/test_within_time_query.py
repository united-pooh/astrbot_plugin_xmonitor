from __future__ import annotations

import ast
import unittest
from pathlib import Path

from config import normalize_check_interval_minutes, validate_check_interval_minutes
from tweet_helpers import build_search_query
from twitter_client import TwitterClient

REPO_ROOT = Path(__file__).resolve().parents[1]
MAIN_PATH = REPO_ROOT / "main.py"


class WithinTimeQueryTest(unittest.TestCase):
    def test_query_uses_configured_within_time_minutes(self) -> None:
        query = build_search_query("@Blue_ArchiveJP", 10)

        self.assertEqual(
            query,
            "from:Blue_ArchiveJP include:nativeretweets within_time:10m",
        )
        self.assertNotIn("since_time:", query)
        self.assertNotIn("until_time:", query)

    def test_check_interval_must_be_positive_minutes(self) -> None:
        with self.assertRaises(ValueError):
            validate_check_interval_minutes(0)
        with self.assertRaises(ValueError):
            validate_check_interval_minutes(-1)

    def test_check_interval_accepts_large_minute_windows(self) -> None:
        self.assertEqual(validate_check_interval_minutes(120), 120)

    def test_check_interval_is_not_reinterpreted_by_value(self) -> None:
        self.assertEqual(normalize_check_interval_minutes(600), (600, False))
        self.assertEqual(normalize_check_interval_minutes(120), (120, False))
        self.assertEqual(normalize_check_interval_minutes(10), (10, False))

    def test_manual_command_uses_astrbot_alias_argument(self) -> None:
        source = MAIN_PATH.read_text()
        module = ast.parse(source)
        class_node = next(
            node
            for node in module.body
            if isinstance(node, ast.ClassDef) and node.name == "XMonitor"
        )
        method_node = next(
            node
            for node in class_node.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "get_latest_tweet_command"
        )
        command_decorators = [
            decorator
            for decorator in method_node.decorator_list
            if (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "command"
            )
        ]

        self.assertEqual(len(command_decorators), 1)
        decorator = command_decorators[0]
        self.assertEqual(decorator.args[0].value, "new")

        alias_keyword = next(
            keyword for keyword in decorator.keywords if keyword.arg == "alias"
        )
        self.assertIsInstance(alias_keyword.value, ast.Set)
        aliases = {
            element.value
            for element in alias_keyword.value.elts
            if isinstance(element, ast.Constant)
        }
        self.assertEqual(aliases, {"newx"})


class FetchPathTest(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_window_uses_latest_query_and_api_key(self) -> None:
        class Response:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {"tweets": []}

        class Client:
            def __init__(self) -> None:
                self.calls = []

            async def get(self, url, *, headers, params):
                self.calls.append((url, headers, params))
                return Response()

        client = Client()
        twitter_client = TwitterClient(
            api_key="secret",
            target_account="@Blue_ArchiveJP",
            check_interval_minutes=10,
        )

        await twitter_client.fetch_search_window(client)

        self.assertEqual(len(client.calls), 1)
        url, headers, params = client.calls[0]
        self.assertEqual(url, twitter_client.TWITTER_SEARCH_URL)
        self.assertEqual(headers, {"X-API-Key": "secret"})
        self.assertEqual(params["queryType"], "Latest")
        self.assertEqual(
            params["query"],
            "from:Blue_ArchiveJP include:nativeretweets within_time:10m",
        )

    async def test_fetch_window_raises_on_api_error_status(self) -> None:
        class Response:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {"status": "error", "message": "bad query"}

        class Client:
            async def get(self, url, *, headers, params):
                return Response()

        twitter_client = TwitterClient(
            api_key="secret",
            target_account="Blue_ArchiveJP",
            check_interval_minutes=10,
        )

        with self.assertRaisesRegex(RuntimeError, "bad query"):
            await twitter_client.fetch_search_window(Client())

    async def test_fetch_new_tweets_dedupes_and_sorts_response(self) -> None:
        class Response:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {
                    "tweets": [
                        {
                            "id": "2",
                            "createdAt": "Wed May 01 00:02:00 +0000 2024",
                            "text": "newer",
                        },
                        {
                            "id": "1",
                            "createdAt": "Wed May 01 00:01:00 +0000 2024",
                            "text": "older",
                        },
                        {
                            "id": "2",
                            "createdAt": "Wed May 01 00:02:00 +0000 2024",
                            "text": "duplicate",
                        },
                    ]
                }

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def get(self, url, *, headers, params):
                return Response()

        class FakeHttpx:
            @staticmethod
            def AsyncClient(*, timeout):
                return Client()

        twitter_client = TwitterClient(
            api_key="secret",
            target_account="Blue_ArchiveJP",
            check_interval_minutes=10,
            httpx_module=FakeHttpx,
        )

        tweets = await twitter_client.fetch_recent_tweets()

        self.assertEqual([tweet["id"] for tweet in tweets], ["1", "2"])

    async def test_fetch_new_tweets_requires_api_key(self) -> None:
        twitter_client = TwitterClient(
            api_key="",
            target_account="Blue_ArchiveJP",
            check_interval_minutes=10,
        )

        with self.assertRaisesRegex(RuntimeError, "API key"):
            await twitter_client.fetch_recent_tweets()


if __name__ == "__main__":
    unittest.main()
