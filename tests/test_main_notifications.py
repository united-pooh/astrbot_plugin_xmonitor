from __future__ import annotations

import asyncio
import ast
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from history_store import TweetHistoryLookupCollision, TweetHistoryStore

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


def _unwrap(value):
    if isinstance(value, staticmethod):
        return value.__func__
    return value


class FakeMessageChain:
    def __init__(self) -> None:
        self.operations = []

    def at(self, user_id, display_name=None):
        self.operations.append(("at", user_id, display_name))
        return self

    def message(self, text):
        self.operations.append(("message", text))
        return self

    def base64_image(self, payload):
        self.operations.append(("base64_image", payload))
        return self

    def has_operation(self, operation_name: str) -> bool:
        return any(operation[0] == operation_name for operation in self.operations)


class FakeLogger:
    def __init__(self) -> None:
        self.infos = []
        self.warnings = []
        self.errors = []

    def info(self, message):
        self.infos.append(message)

    def warning(self, message):
        self.warnings.append(message)

    def error(self, message):
        self.errors.append(message)


class FakeEvent:
    def __init__(self, message_str: str = "") -> None:
        self.message_str = message_str

    def get_message_str(self) -> str:
        return self.message_str

    def plain_result(self, text):
        return ("plain", text)

    def make_result(self):
        return FakeMessageChain()


class FakeHistoryStore:
    def __init__(self, records=None) -> None:
        self.records = list(records or [])
        self.added = []
        self.lookup_error = None

    def add_tweet(self, tweet, *, account=None):
        self.added.append((tweet, account))
        _full_hash, short_id = TweetHistoryStore.hash_text(tweet.get("text", ""))
        record = FakeHistoryRecord(
            short_id=short_id,
            original_text=tweet.get("text", ""),
            tweet=tweet,
            created_at=tweet.get("createdAt"),
            stored_at="2024-05-01T09:02:00+08:00",
        )
        self.records.insert(0, record)
        return record

    def list_recent(self, limit=10):
        return self.records[:limit]

    def get_by_short_id(self, short_id):
        if self.lookup_error is not None:
            raise self.lookup_error
        normalized = TweetHistoryStore.normalize_short_id(short_id)
        for record in self.records:
            if record.short_id == normalized:
                return record
        return None


class FakeHistoryRecord:
    def __init__(
        self,
        *,
        short_id,
        original_text,
        tweet,
        created_at=None,
        stored_at="2024-05-01T09:02:00+08:00",
    ) -> None:
        self.short_id = short_id
        self.original_text = original_text
        self.tweet = tweet
        self.created_at = created_at
        self.stored_at = stored_at


def _tweet(tweet_id: str, text: str) -> dict:
    return {
        "id": tweet_id,
        "text": text,
        "createdAt": "Wed May 01 01:02:00 +0000 2024",
    }


async def _collect_async(async_iterable):
    return [item async for item in async_iterable]


def _build_probe(render_to_base64_func, *, fail_image_for_groups=None):
    methods = _load_xmonitor_methods()
    logger = FakeLogger()

    class FakeStarTools:
        calls = []
        fail_image_for = set(fail_image_for_groups or [])

        @classmethod
        async def send_message_by_id(cls, *, type, id, message_chain):
            cls.calls.append(
                {
                    "type": type,
                    "id": id,
                    "message_chain": message_chain,
                }
            )
            if id in cls.fail_image_for and message_chain.has_operation("base64_image"):
                raise RuntimeError("image send failed")

    namespace: dict[str, object] = {
        "datetime": datetime,
        "timedelta": timedelta,
        "timezone": timezone,
        "Path": Path,
        "MessageChain": FakeMessageChain,
        "StarTools": FakeStarTools,
        "asyncio": asyncio,
        "logger": logger,
        "PLUGIN_DIR": REPO_ROOT,
        "DEFAULT_FONT_DIR": REPO_ROOT / "data" / "fonts",
        "DEFAULT_FONT_DOWNLOADS": (),
        "_download_font_file": lambda url, output_path: None,
        "render_to_base64": render_to_base64_func,
        "re": __import__("re"),
        "TweetHistoryStore": TweetHistoryStore,
        "TweetHistoryLookupCollision": TweetHistoryLookupCollision,
    }
    for method_name in (
        "_extract_tweet_id",
        "_sanitize_tweet_text",
        "_parse_tweet_datetime",
        "_normalize_source_logo",
        "_normalize_bool",
        "_normalize_path_list",
        "_refresh_render_font_settings",
        "_start_font_bootstrap_task",
        "_ensure_render_fonts",
        "_wait_for_font_bootstrap",
        "_format_created_at",
        "_build_tweet_display_lines",
        "_build_notification_message",
        "_build_text_message_chain",
        "_build_tweet_image_message_chain",
        "_build_render_options",
        "_render_tweet_to_base64",
        "_send_text_fallback",
        "notify_subscribers",
        "_store_tweets_history",
        "_normalize_history_short_id",
        "_parse_x_command",
        "_summarize_history_text",
        "_build_history_list_message",
        "_render_history_record_to_base64",
        "get_latest_tweet_command",
        "get_history_command",
        "render_history_tweet_command",
        "check_for_new_tweets",
    ):
        exec(
            "from __future__ import annotations\n" + methods[method_name],
            namespace,
        )

    class Probe:
        _extract_tweet_id = staticmethod(_unwrap(namespace["_extract_tweet_id"]))
        _sanitize_tweet_text = staticmethod(_unwrap(namespace["_sanitize_tweet_text"]))
        _parse_tweet_datetime = staticmethod(
            _unwrap(namespace["_parse_tweet_datetime"])
        )
        _normalize_source_logo = staticmethod(
            _unwrap(namespace["_normalize_source_logo"])
        )
        _normalize_bool = staticmethod(_unwrap(namespace["_normalize_bool"]))
        _normalize_path_list = staticmethod(_unwrap(namespace["_normalize_path_list"]))
        _refresh_render_font_settings = _unwrap(
            namespace["_refresh_render_font_settings"]
        )
        _start_font_bootstrap_task = _unwrap(namespace["_start_font_bootstrap_task"])
        _ensure_render_fonts = _unwrap(namespace["_ensure_render_fonts"])
        _wait_for_font_bootstrap = _unwrap(namespace["_wait_for_font_bootstrap"])
        _format_created_at = _unwrap(namespace["_format_created_at"])
        _build_tweet_display_lines = _unwrap(namespace["_build_tweet_display_lines"])
        _build_notification_message = _unwrap(namespace["_build_notification_message"])
        _build_text_message_chain = _unwrap(namespace["_build_text_message_chain"])
        _build_tweet_image_message_chain = _unwrap(
            namespace["_build_tweet_image_message_chain"]
        )
        _build_render_options = _unwrap(namespace["_build_render_options"])
        _render_tweet_to_base64 = _unwrap(namespace["_render_tweet_to_base64"])
        _send_text_fallback = _unwrap(namespace["_send_text_fallback"])
        notify_subscribers = _unwrap(namespace["notify_subscribers"])
        _store_tweets_history = _unwrap(namespace["_store_tweets_history"])
        _normalize_history_short_id = staticmethod(
            _unwrap(namespace["_normalize_history_short_id"])
        )
        _parse_x_command = staticmethod(_unwrap(namespace["_parse_x_command"]))
        _summarize_history_text = staticmethod(
            _unwrap(namespace["_summarize_history_text"])
        )
        _build_history_list_message = _unwrap(namespace["_build_history_list_message"])
        _render_history_record_to_base64 = _unwrap(
            namespace["_render_history_record_to_base64"]
        )
        get_latest_tweet_command = _unwrap(namespace["get_latest_tweet_command"])
        get_history_command = _unwrap(namespace["get_history_command"])
        render_history_tweet_command = _unwrap(
            namespace["render_history_tweet_command"]
        )
        check_for_new_tweets = _unwrap(namespace["check_for_new_tweets"])

    return Probe, FakeStarTools, logger


class MainNotificationTest(unittest.IsolatedAsyncioTestCase):
    def test_installed_message_chain_base64_image_component_shape(self) -> None:
        source_path = next(
            (
                Path(entry)
                / "astrbot"
                / "core"
                / "message"
                / "message_event_result.py"
                for entry in sys.path
                if (
                    Path(entry)
                    / "astrbot"
                    / "core"
                    / "message"
                    / "message_event_result.py"
                ).exists()
            ),
            None,
        )

        self.assertIsNotNone(source_path)
        source = source_path.read_text()
        self.assertIn("def base64_image", source)
        self.assertIn("Image.fromBase64(base64_str)", source)

    async def test_notify_subscribers_sends_base64_image_with_mention(self) -> None:
        Probe, StarTools, _logger = _build_probe(
            lambda tweet, options=None: f"png-{tweet['id']}"
        )
        probe = Probe()
        probe.subscribe_groups = ["group-1"]
        probe.notify_user = "user-9"
        probe.target_account = "Blue_ArchiveJP"

        await probe.notify_subscribers([_tweet("1", "hello")])

        self.assertEqual(len(StarTools.calls), 1)
        call = StarTools.calls[0]
        self.assertEqual(call["type"], "GroupMessage")
        self.assertEqual(call["id"], "group-1")
        self.assertEqual(
            call["message_chain"].operations,
            [("at", "user-9", "user-9"), ("base64_image", "png-1")],
        )

    async def test_notify_subscribers_passes_source_logo_to_renderer(self) -> None:
        captured_options = []

        def render(tweet, options=None):
            captured_options.append(options)
            return f"png-{tweet['id']}"

        Probe, StarTools, _logger = _build_probe(render)
        probe = Probe()
        probe.subscribe_groups = ["group-1"]
        probe.notify_user = None
        probe.target_account = "Blue_ArchiveJP"
        probe.source_logo = "/tmp/xmonitor-source-logo.png"

        await probe.notify_subscribers([_tweet("1", "hello")])

        self.assertEqual(len(StarTools.calls), 1)
        self.assertEqual(
            captured_options,
            [{"source_logo": "/tmp/xmonitor-source-logo.png"}],
        )

    async def test_render_failure_falls_back_to_text_and_continues(self) -> None:
        def render(tweet, options=None):
            if tweet["id"] == "bad":
                raise RuntimeError("render boom")
            return f"png-{tweet['id']}"

        Probe, StarTools, _logger = _build_probe(render)
        probe = Probe()
        probe.subscribe_groups = ["group-1"]
        probe.notify_user = None
        probe.target_account = "Blue_ArchiveJP"

        await probe.notify_subscribers(
            [_tweet("bad", "bad tweet"), _tweet("good", "good tweet")]
        )

        self.assertEqual(len(StarTools.calls), 2)
        self.assertEqual(
            StarTools.calls[0]["message_chain"].operations[0][0], "message"
        )
        self.assertIn("bad tweet", StarTools.calls[0]["message_chain"].operations[0][1])
        self.assertEqual(
            StarTools.calls[1]["message_chain"].operations,
            [("base64_image", "png-good")],
        )

    async def test_image_send_failure_falls_back_per_group(self) -> None:
        Probe, StarTools, _logger = _build_probe(
            lambda tweet, options=None: f"png-{tweet['id']}",
            fail_image_for_groups={"group-1"},
        )
        probe = Probe()
        probe.subscribe_groups = ["group-1", "group-2"]
        probe.notify_user = "user-9"
        probe.target_account = "Blue_ArchiveJP"

        await probe.notify_subscribers([_tweet("1", "fallback please")])

        self.assertEqual(len(StarTools.calls), 3)
        self.assertEqual(StarTools.calls[0]["id"], "group-1")
        self.assertTrue(
            StarTools.calls[0]["message_chain"].has_operation("base64_image")
        )
        self.assertEqual(StarTools.calls[1]["id"], "group-1")
        self.assertTrue(StarTools.calls[1]["message_chain"].has_operation("message"))
        self.assertEqual(StarTools.calls[2]["id"], "group-2")
        self.assertTrue(
            StarTools.calls[2]["message_chain"].has_operation("base64_image")
        )

    def test_parse_x_command_preserves_translation_text(self) -> None:
        Probe, _StarTools, _logger = _build_probe(lambda tweet, options=None: "png")

        short_id, translation = Probe._parse_x_command(
            "/x 114514 翻译正文 https://example.test\n#测试 😀"
        )

        self.assertEqual(short_id, "114514")
        self.assertEqual(translation, "翻译正文 https://example.test\n#测试 😀")

    async def test_manual_fetch_records_history_before_reply(self) -> None:
        Probe, _StarTools, _logger = _build_probe(lambda tweet, options=None: "png")
        probe = Probe()
        probe.target_account = "Blue_ArchiveJP"
        probe.check_interval_minutes = 10
        probe.history_store = FakeHistoryStore()

        async def fetch():
            return [_tweet("1", "manual tweet")]

        probe._fetch_new_tweets = fetch

        results = await _collect_async(
            probe.get_latest_tweet_command(FakeEvent("new"))
        )

        self.assertEqual(len(probe.history_store.added), 1)
        self.assertEqual(probe.history_store.added[0][1], "Blue_ArchiveJP")
        self.assertEqual(results[0][0], "plain")
        self.assertIn("manual tweet", results[0][1])

    async def test_scheduled_fetch_records_history_before_notify(self) -> None:
        Probe, _StarTools, _logger = _build_probe(lambda tweet, options=None: "png")
        probe = Probe()
        probe.target_account = "Blue_ArchiveJP"
        probe.check_interval_minutes = 10
        probe.history_store = FakeHistoryStore()
        notified = []

        async def fetch():
            return [_tweet("1", "scheduled tweet")]

        async def notify(tweets):
            notified.append(list(tweets))

        probe._fetch_new_tweets = fetch
        probe.notify_subscribers = notify

        await probe.check_for_new_tweets()

        self.assertEqual(len(probe.history_store.added), 1)
        self.assertEqual(notified, [[_tweet("1", "scheduled tweet")]])

    async def test_history_command_lists_latest_records(self) -> None:
        Probe, _StarTools, _logger = _build_probe(lambda tweet, options=None: "png")
        first = FakeHistoryRecord(
            short_id="aaaaaa",
            original_text="older text",
            tweet=_tweet("1", "older text"),
            created_at="2024-05-01 09:01:00",
        )
        second = FakeHistoryRecord(
            short_id="bbbbbb",
            original_text="newer text",
            tweet=_tweet("2", "newer text"),
            created_at="2024-05-01 09:02:00",
        )
        probe = Probe()
        probe.history_store = FakeHistoryStore([second, first])

        results = await _collect_async(probe.get_history_command(FakeEvent("history")))

        self.assertEqual(results[0][0], "plain")
        self.assertIn("#bbbbbb", results[0][1])
        self.assertLess(results[0][1].index("#bbbbbb"), results[0][1].index("#aaaaaa"))

    async def test_x_command_renders_original_history_tweet_as_image(self) -> None:
        captured = []

        def render(tweet, options=None):
            captured.append((tweet, options))
            return f"png-{tweet['id']}"

        Probe, _StarTools, _logger = _build_probe(render)
        record = FakeHistoryRecord(
            short_id="114514",
            original_text="original tweet",
            tweet=_tweet("1", "original tweet"),
        )
        probe = Probe()
        probe.source_logo = "/tmp/source-logo.png"
        probe.history_store = FakeHistoryStore([record])

        results = await _collect_async(
            probe.render_history_tweet_command(FakeEvent("/x 114514"))
        )

        self.assertEqual(
            results[0].operations,
            [("base64_image", "png-1")],
        )
        self.assertEqual(captured, [(record.tweet, {"source_logo": "/tmp/source-logo.png"})])

    async def test_x_command_renders_translation_with_original_assets(self) -> None:
        captured = []

        def render(tweet, options=None):
            captured.append((tweet, options))
            return "png-translated"

        Probe, _StarTools, _logger = _build_probe(render)
        record = FakeHistoryRecord(
            short_id="114514",
            original_text="original tweet",
            tweet=_tweet("1", "original tweet"),
        )
        probe = Probe()
        probe.source_logo = None
        probe.history_store = FakeHistoryStore([record])

        results = await _collect_async(
            probe.render_history_tweet_command(
                FakeEvent("/x 114514 翻译正文 https://example.test\n#测试 😀")
            )
        )

        self.assertEqual(results[0].operations, [("base64_image", "png-translated")])
        self.assertEqual(captured[0][0], record.tweet)
        self.assertEqual(
            captured[0][1],
            {
                "text_override": "翻译正文 https://example.test\n#测试 😀",
                "translation_style": True,
            },
        )

    async def test_x_command_returns_clear_not_found_and_collision_messages(self) -> None:
        Probe, _StarTools, _logger = _build_probe(lambda tweet, options=None: "png")
        probe = Probe()
        probe.history_store = FakeHistoryStore()

        missing = await _collect_async(
            probe.render_history_tweet_command(FakeEvent("/x 114514"))
        )

        self.assertEqual(missing[0][0], "plain")
        self.assertIn("未找到 #114514", missing[0][1])

        probe.history_store.lookup_error = TweetHistoryLookupCollision("短 ID 冲突")
        collided = await _collect_async(
            probe.render_history_tweet_command(FakeEvent("/x 114514"))
        )

        self.assertEqual(collided[0], ("plain", "短 ID 冲突"))


if __name__ == "__main__":
    unittest.main()
