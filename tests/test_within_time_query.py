from __future__ import annotations

import ast
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MAIN_PATH = REPO_ROOT / "main.py"


def _load_xmonitor_methods() -> dict[str, str]:
    source = MAIN_PATH.read_text()
    module = ast.parse(source)
    class_node = next(
        node
        for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "XMonitor"
    )
    return {
        node.name: ast.get_source_segment(source, node)
        for node in class_node.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


class WithinTimeQueryTest(unittest.TestCase):
    def test_query_uses_configured_within_time_minutes(self) -> None:
        methods = _load_xmonitor_methods()
        namespace: dict[str, object] = {}
        exec(
            "from __future__ import annotations\n"
            + methods["_validate_check_interval_minutes"],
            namespace,
        )
        exec(methods["_target_account_name"], namespace)
        exec(methods["_build_search_query"], namespace)

        class Probe:
            _validate_check_interval_minutes = staticmethod(
                namespace["_validate_check_interval_minutes"]
            )
            _target_account_name = namespace["_target_account_name"]
            _build_search_query = namespace["_build_search_query"]

        probe = Probe()
        probe.target_account = "@Blue_ArchiveJP"
        probe.check_interval_minutes = probe._validate_check_interval_minutes(10)

        self.assertEqual(
            probe._build_search_query(),
            "from:Blue_ArchiveJP include:nativeretweets within_time:10m",
        )
        self.assertNotIn("since_time:", probe._build_search_query())
        self.assertNotIn("until_time:", probe._build_search_query())

    def test_check_interval_must_be_positive_minutes(self) -> None:
        methods = _load_xmonitor_methods()
        namespace: dict[str, object] = {}
        exec(
            "from __future__ import annotations\n"
            + methods["_validate_check_interval_minutes"],
            namespace,
        )

        validate = namespace["_validate_check_interval_minutes"]
        with self.assertRaises(ValueError):
            validate(0)
        with self.assertRaises(ValueError):
            validate(-1)

    def test_check_interval_is_always_interpreted_as_minutes(self) -> None:
        methods = _load_xmonitor_methods()
        namespace: dict[str, object] = {}
        exec(
            "from __future__ import annotations\n"
            + methods["_validate_check_interval_minutes"],
            namespace,
        )

        validate = namespace["_validate_check_interval_minutes"]
        self.assertEqual(validate(120), 120)

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
    def _build_probe_class(self, httpx_module=None):
        methods = _load_xmonitor_methods()
        namespace: dict[str, object] = {
            "datetime": datetime,
            "timedelta": timedelta,
            "timezone": timezone,
            "httpx": httpx_module,
        }
        for method_name in (
            "_validate_check_interval_minutes",
            "_target_account_name",
            "_build_search_query",
            "_extract_tweet_id",
            "_parse_tweet_datetime",
            "_dedupe_tweets",
            "_fetch_tweet_search_window",
            "_fetch_new_tweets",
        ):
            exec(
                "from __future__ import annotations\n" + methods[method_name],
                namespace,
            )

        class Probe:
            TWITTER_SEARCH_URL = (
                "https://api.twitterapi.io/twitter/tweet/advanced_search"
            )
            _validate_check_interval_minutes = staticmethod(
                namespace["_validate_check_interval_minutes"]
            )
            _extract_tweet_id = staticmethod(namespace["_extract_tweet_id"])
            _parse_tweet_datetime = staticmethod(namespace["_parse_tweet_datetime"])
            _target_account_name = namespace["_target_account_name"]
            _build_search_query = namespace["_build_search_query"]
            _dedupe_tweets = namespace["_dedupe_tweets"]
            _fetch_tweet_search_window = namespace["_fetch_tweet_search_window"]
            _fetch_new_tweets = namespace["_fetch_new_tweets"]

            async def _ensure_target_avatar_cached(self, client):
                self.avatar_cache_client = client

        return Probe

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

        Probe = self._build_probe_class()
        probe = Probe()
        probe.api_key = "secret"
        probe.target_account = "@Blue_ArchiveJP"
        probe.check_interval_minutes = 10
        client = Client()

        await probe._fetch_tweet_search_window(client)

        self.assertEqual(len(client.calls), 1)
        url, headers, params = client.calls[0]
        self.assertEqual(url, probe.TWITTER_SEARCH_URL)
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

        Probe = self._build_probe_class()
        probe = Probe()
        probe.api_key = "secret"
        probe.target_account = "Blue_ArchiveJP"
        probe.check_interval_minutes = 10

        with self.assertRaisesRegex(RuntimeError, "bad query"):
            await probe._fetch_tweet_search_window(Client())

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

        Probe = self._build_probe_class(FakeHttpx)
        probe = Probe()
        probe.api_key = "secret"
        probe.target_account = "Blue_ArchiveJP"
        probe.check_interval_minutes = 10

        tweets = await probe._fetch_new_tweets()

        self.assertEqual([tweet["id"] for tweet in tweets], ["1", "2"])
        self.assertIsInstance(probe.avatar_cache_client, Client)

    async def test_fetch_new_tweets_requires_api_key(self) -> None:
        Probe = self._build_probe_class()
        probe = Probe()
        probe.api_key = ""
        probe.target_account = "Blue_ArchiveJP"
        probe.check_interval_minutes = 10

        with self.assertRaisesRegex(RuntimeError, "API key"):
            await probe._fetch_new_tweets()


if __name__ == "__main__":
    unittest.main()
