from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

from command_service import (
    CommandService,
    event_group_id,
    parse_x_command,
    parse_xmonitor_args,
)
from config import build_config_status_message, load_runtime_config
from history_service import HistoryService, StoredTweet
from history_store import TweetHistoryLookupCollision, TweetHistoryStore
from message_builder import MessageBuilder
from notification_sender import NotificationSender
from render_service import RenderService
from translation_service import TranslationService

REPO_ROOT = Path(__file__).resolve().parents[1]


class FakeMessageChain:
    def __init__(self) -> None:
        self.operations: list[tuple[Any, ...]] = []

    def at(self, user_id, display_name=None):
        self.operations.append(("at", user_id, display_name))
        return self

    def message(self, text):
        self.operations.append(("message", text))
        return self

    def base64_image(self, payload):
        self.operations.append(("base64_image", payload))
        return self

    def url_image(self, url):
        self.operations.append(("url_image", url))
        return self

    def has_operation(self, operation_name: str) -> bool:
        return any(operation[0] == operation_name for operation in self.operations)


class FakeLogger:
    def __init__(self) -> None:
        self.infos: list[str] = []
        self.warnings: list[str] = []
        self.errors: list[str] = []

    def info(self, message):
        self.infos.append(message)

    def warning(self, message):
        self.warnings.append(message)

    def error(self, message):
        self.errors.append(message)


class FakeConfig(dict):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.save_count = 0

    def save_config(self) -> None:
        self.save_count += 1


class FakeEvent:
    def __init__(self, message_str: str = "", group_id: str = "") -> None:
        self.message_str = message_str
        self.group_id = group_id

    def get_message_str(self) -> str:
        return self.message_str

    def get_group_id(self) -> str:
        return self.group_id

    def plain_result(self, text):
        return ("plain", text)

    def make_result(self):
        return FakeMessageChain()


class FakeHistoryRecord:
    def __init__(
        self,
        *,
        short_id: str,
        full_hash: str | None = None,
        original_text: str,
        tweet: dict[str, Any],
        created_at: str | None = None,
        stored_at: str = "2024-05-01T09:02:00+08:00",
    ) -> None:
        self.short_id = short_id
        self.full_hash = full_hash or TweetHistoryStore.hash_text(original_text)[0]
        self.original_text = original_text
        self.tweet = tweet
        self.created_at = created_at
        self.stored_at = stored_at


class FakeHistoryStore:
    def __init__(self, records=None) -> None:
        self.records = list(records or [])
        self.added = []
        self.lookup_error = None

    def add_tweet(self, tweet, *, account=None):
        self.added.append((tweet, account))
        full_hash, short_id = TweetHistoryStore.hash_text(tweet.get("text", ""))
        record = FakeHistoryRecord(
            short_id=short_id,
            full_hash=full_hash,
            original_text=tweet.get("text", ""),
            tweet=tweet,
            created_at=tweet.get("createdAt"),
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

    def get_by_full_hash(self, full_hash):
        normalized = str(full_hash or "").strip().lower()
        for record in self.records:
            if record.full_hash == normalized:
                return record
        return None


class FakeTwitterClient:
    def __init__(self, tweets=None, profile=None, profile_error=None) -> None:
        self.tweets = list(tweets or [])
        self.profile = profile
        self.profile_error = profile_error

    async def fetch_recent_tweets(self):
        return list(self.tweets)

    async def fetch_user_profile(self, user_name: str):
        if self.profile_error is not None:
            raise self.profile_error
        return self.profile


def _tweet(tweet_id: str, text: str) -> dict[str, Any]:
    return {
        "id": tweet_id,
        "text": text,
        "createdAt": "Wed May 01 01:02:00 +0000 2024",
    }


def _stored_tweet(tweet: dict[str, Any]) -> StoredTweet:
    full_hash, short_id = TweetHistoryStore.hash_text(str(tweet.get("text") or ""))
    record = FakeHistoryRecord(
        short_id=short_id,
        full_hash=full_hash,
        original_text=str(tweet.get("text") or ""),
        tweet=tweet,
        created_at=tweet.get("createdAt"),
    )
    return StoredTweet(tweet=tweet, record=record)


def _runtime_config(**overrides) -> FakeConfig:
    config = FakeConfig(
        {
            "X-API": "secret",
            "TARGET_ACCOUNT": "Blue_ArchiveJP",
            "CHECK_INTERVAL": 10,
            "SUBSCRIBE_GROUPS": [],
            "NOTIFY_USER": "",
            "SOURCE_LOGO": "",
            "ENABLE_AUTO_TRANSLATION": False,
            "TRANSLATION_PROVIDER_ID": "",
            "TRANSLATION_SYSTEM_PROMPT": "默认翻译提示",
            "GROUP_RENDER_OVERRIDES": {},
        }
    )
    config.update(overrides)
    return config


def _command_service(
    *,
    config: FakeConfig | None = None,
    history_store: FakeHistoryStore | None = None,
    twitter_client: FakeTwitterClient | None = None,
    render_func=None,
) -> CommandService:
    config = config or _runtime_config()
    runtime_config = load_runtime_config(config, base_dir=REPO_ROOT)
    history_service = HistoryService(
        history_store or FakeHistoryStore(),
        account=runtime_config.target_account,
    )
    render_service = RenderService(
        source_logo=runtime_config.source_logo,
        render_func=render_func or (lambda tweet, options=None: f"png-{tweet['id']}"),
    )
    message_builder = MessageBuilder(
        target_account=runtime_config.target_account,
        notify_user=runtime_config.notify_user,
        chain_factory=FakeMessageChain,
    )

    def reload_runtime():
        return load_runtime_config(config, base_dir=REPO_ROOT)

    return CommandService(
        runtime_config=runtime_config,
        config=config,
        base_dir=REPO_ROOT,
        twitter_client=twitter_client or FakeTwitterClient(),
        history_service=history_service,
        render_service=render_service,
        message_builder=message_builder,
        on_config_changed=reload_runtime,
    )


def _notification_sender(
    *,
    render_func,
    subscribe_groups=None,
    notify_user=None,
    source_logo=None,
    group_overrides=None,
    translation_enabled=False,
    provider_id="provider-1",
    translate_func=None,
    fail_image_for_groups=None,
    fail_source_for_groups=None,
):
    calls = []
    fail_image_for_groups = set(fail_image_for_groups or [])
    fail_source_for_groups = set(fail_source_for_groups or [])

    async def send_message(*, type, id, message_chain):
        calls.append({"type": type, "id": id, "message_chain": message_chain})
        if id in fail_image_for_groups and message_chain.has_operation("base64_image"):
            raise RuntimeError("image send failed")
        if id in fail_source_for_groups and not message_chain.has_operation(
            "base64_image"
        ):
            if message_chain.operations == [("message", "source breaks")]:
                raise RuntimeError("source send failed")

    if translate_func is None:

        async def translate_func(*args, **kwargs):
            return None

    sender = NotificationSender(
        subscribe_groups=subscribe_groups or ["group-1"],
        target_account="Blue_ArchiveJP",
        render_service=RenderService(source_logo=source_logo, render_func=render_func),
        translation_service=TranslationService(
            context=object(),
            enabled=translation_enabled,
            provider_id=provider_id,
            system_prompt="默认翻译提示",
            translate_func=translate_func,
        ),
        message_builder=MessageBuilder(
            target_account="Blue_ArchiveJP",
            notify_user=notify_user,
            chain_factory=FakeMessageChain,
        ),
        send_message=send_message,
        render_options_for_group=lambda group_id: (group_overrides or {}).get(group_id),
    )
    return sender, calls


class MainNotificationTest(unittest.IsolatedAsyncioTestCase):
    def test_installed_message_chain_base64_image_component_shape(self) -> None:
        source_path = next(
            (
                Path(entry) / "astrbot" / "core" / "message" / "message_event_result.py"
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
        sender, calls = _notification_sender(
            render_func=lambda tweet, options=None: f"png-{tweet['id']}",
            notify_user="user-9",
        )
        tweet = _tweet("2059522313964679235", "hello\nworld")
        tweet["media"] = [{"media_url_https": "https://pbs.twimg.com/media/a.jpg"}]
        expected_short_id = TweetHistoryStore.hash_text(tweet["text"])[1]

        debug = await sender.notify([_stored_tweet(tweet)])

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["type"], "GroupMessage")
        self.assertEqual(calls[0]["id"], "group-1")
        self.assertEqual(
            calls[0]["message_chain"].operations,
            [
                ("at", "user-9", "user-9"),
                ("message", f"#{expected_short_id}"),
                ("base64_image", "png-2059522313964679235"),
            ],
        )
        self.assertEqual(
            calls[1]["message_chain"].operations,
            [
                ("message", "hello\nworld"),
                ("url_image", "https://pbs.twimg.com/media/a.jpg"),
            ],
        )
        self.assertEqual(debug.results[0].short_id, expected_short_id)
        self.assertEqual(debug.results[0].render_status, "success")
        self.assertEqual(debug.results[0].image_send.status, "success")
        self.assertEqual(debug.results[0].source_send.status, "success")

    async def test_render_service_passes_source_logo_to_renderer(self) -> None:
        captured_options = []

        def render(tweet, options=None):
            captured_options.append(options)
            return f"png-{tweet['id']}"

        service = RenderService(
            source_logo="/tmp/xmonitor-source-logo.png", render_func=render
        )

        await service.render_tweet_to_base64(_tweet("1", "hello"))

        self.assertEqual(
            captured_options,
            [{"source_logo": "/tmp/xmonitor-source-logo.png"}],
        )

    async def test_notify_subscribers_applies_group_render_overrides(self) -> None:
        captured_options = []

        def render(tweet, options=None):
            captured_options.append((tweet["id"], options))
            return f"png-{tweet['id']}"

        sender, calls = _notification_sender(
            render_func=render,
            subscribe_groups=["group-1", "group-2"],
            group_overrides={
                "group-1": {
                    "avatar": "https://pbs.twimg.com/avatar1.jpg",
                    "display_name": "群一显示名",
                    "username": "GroupOne",
                },
                "group-2": {
                    "avatar": "https://pbs.twimg.com/avatar2.jpg",
                    "display_name": "群二显示名",
                    "username": "GroupTwo",
                },
            },
        )

        await sender.notify([_stored_tweet(_tweet("1", "hello"))])

        self.assertEqual(len(calls), 4)
        self.assertEqual(
            captured_options,
            [
                (
                    "1",
                    {
                        "avatar": "https://pbs.twimg.com/avatar1.jpg",
                        "display_name": "群一显示名",
                        "username": "GroupOne",
                    },
                ),
                (
                    "1",
                    {
                        "avatar": "https://pbs.twimg.com/avatar2.jpg",
                        "display_name": "群二显示名",
                        "username": "GroupTwo",
                    },
                ),
            ],
        )

    async def test_auto_translation_is_disabled_by_default(self) -> None:
        async def translate_func(*args, **kwargs):
            raise AssertionError("translation should not be called")

        sender, calls = _notification_sender(
            render_func=lambda tweet, options=None: "png",
            translate_func=translate_func,
        )

        await sender.notify([_stored_tweet(_tweet("1", "hello"))])

        self.assertEqual(len(calls), 2)

    async def test_auto_translation_renders_translated_text_once_per_tweet(
        self,
    ) -> None:
        captured_options = []
        translated_tweets = []

        def render(tweet, options=None):
            captured_options.append(options)
            return f"png-{tweet['id']}"

        async def translate_func(context, *, chat_provider_id, tweet, system_prompt):
            translated_tweets.append(tweet["id"])
            return f"译文 {tweet['id']}"

        sender, calls = _notification_sender(
            render_func=render,
            subscribe_groups=["group-1", "group-2"],
            translation_enabled=True,
            translate_func=translate_func,
        )

        await sender.notify([_stored_tweet(_tweet("1", "hello"))])

        self.assertEqual(translated_tweets, ["1"])
        self.assertEqual(len(calls), 4)
        self.assertEqual(
            captured_options,
            [
                {"text_override": "译文 1", "translation_style": True},
                {"text_override": "译文 1", "translation_style": True},
            ],
        )
        self.assertEqual(calls[1]["message_chain"].operations, [("message", "hello")])
        self.assertEqual(calls[3]["message_chain"].operations, [("message", "hello")])

    async def test_auto_translation_failure_falls_back_to_original_render(self) -> None:
        captured_options = []

        def render(tweet, options=None):
            captured_options.append(options)
            return f"png-{tweet['id']}"

        async def translate_func(context, *, chat_provider_id, tweet, system_prompt):
            return None

        sender, calls = _notification_sender(
            render_func=render,
            translation_enabled=True,
            translate_func=translate_func,
        )

        debug = await sender.notify([_stored_tweet(_tweet("1", "hello"))])

        self.assertEqual(len(calls), 2)
        self.assertEqual(captured_options, [None])
        self.assertEqual(debug.results[0].translation_status, "empty")

    async def test_config_translation_command_persists_switch(self) -> None:
        config = _runtime_config()
        service = _command_service(config=config)

        enabled = await service.handle_xmonitor_config(
            FakeEvent("/xmonitor config translation on", group_id="group-1"),
            parse_xmonitor_args("/xmonitor config translation on"),
        )

        self.assertEqual(enabled, "定时推送自动翻译已开启。")
        self.assertTrue(config["ENABLE_AUTO_TRANSLATION"])
        self.assertTrue(service.runtime_config.auto_translation_enabled)
        self.assertEqual(config.save_count, 1)

        disabled = await service.handle_xmonitor_config(
            FakeEvent("/xmonitor config translation off", group_id="group-1"),
            parse_xmonitor_args("/xmonitor config translation off"),
        )

        self.assertEqual(disabled, "定时推送自动翻译已关闭。")
        self.assertFalse(config["ENABLE_AUTO_TRANSLATION"])
        self.assertFalse(service.runtime_config.auto_translation_enabled)
        self.assertEqual(config.save_count, 2)

    async def test_runtime_config_preserves_minute_interval(self) -> None:
        config = _runtime_config(CHECK_INTERVAL=600)

        runtime_config = load_runtime_config(config, base_dir=REPO_ROOT)

        self.assertEqual(runtime_config.check_interval_minutes, 600)
        self.assertEqual(config["CHECK_INTERVAL"], 600)
        self.assertEqual(config.save_count, 0)

    async def test_config_status_reports_current_group_override(self) -> None:
        config = _runtime_config(
            ENABLE_AUTO_TRANSLATION=True,
            TRANSLATION_PROVIDER_ID="provider-1",
            GROUP_RENDER_OVERRIDES={
                "group-1": {
                    "display_name": "群名",
                    "username": "group_user",
                    "avatar": "https://pbs.twimg.com/avatar.jpg",
                }
            },
        )
        runtime_config = load_runtime_config(config, base_dir=REPO_ROOT)

        result = build_config_status_message(runtime_config, "group-1")

        self.assertIn("自动翻译：开启", result)
        self.assertIn("翻译 Provider：provider-1", result)
        self.assertIn("当前群 group-1：群名 / @group_user / 头像已配置", result)

    async def test_runtime_config_reads_template_list_group_overrides(self) -> None:
        config = _runtime_config(
            GROUP_RENDER_OVERRIDES=[
                {
                    "__template_key": "group",
                    "group_id": "group-1",
                    "display_name": "群名",
                    "username": "group_user",
                    "avatar": "https://pbs.twimg.com/avatar.jpg",
                }
            ],
        )

        runtime_config = load_runtime_config(config, base_dir=REPO_ROOT)

        self.assertEqual(
            runtime_config.render_options_for_group("group-1"),
            {
                "avatar": "https://pbs.twimg.com/avatar.jpg",
                "display_name": "群名",
                "username": "group_user",
            },
        )

    async def test_config_identity_command_updates_group_override(self) -> None:
        config = _runtime_config()
        service = _command_service(config=config)

        result = await service.handle_xmonitor_config(
            FakeEvent("/xmonitor config identity group-1 群渲染名 group_user"),
            parse_xmonitor_args(
                "/xmonitor config identity group-1 群渲染名 group_user"
            ),
        )

        self.assertIn("已为群 group-1 配置显示身份", result)
        self.assertEqual(
            config["GROUP_RENDER_OVERRIDES"],
            [
                {
                    "__template_key": "group",
                    "group_id": "group-1",
                    "display_name": "群渲染名",
                    "username": "group_user",
                }
            ],
        )
        self.assertEqual(config.save_count, 1)

    async def test_config_avatar_current_fetches_profile_picture(self) -> None:
        config = _runtime_config()
        service = _command_service(
            config=config,
            twitter_client=FakeTwitterClient(
                profile={
                    "name": "Blue Archive",
                    "userName": "Blue_ArchiveJP",
                    "profilePicture": "https://pbs.twimg.com/profile.jpg",
                }
            ),
        )

        result = await service.handle_xmonitor_config(
            FakeEvent(
                "/xmonitor config avatar current Blue_ArchiveJP", group_id="group-1"
            ),
            parse_xmonitor_args("/xmonitor config avatar current Blue_ArchiveJP"),
        )

        self.assertIn("已为群 group-1 配置头像", result)
        self.assertEqual(
            config["GROUP_RENDER_OVERRIDES"],
            [
                {
                    "__template_key": "group",
                    "group_id": "group-1",
                    "user_name": "Blue_ArchiveJP",
                    "display_name": "Blue Archive",
                    "username": "Blue_ArchiveJP",
                    "avatar": "https://pbs.twimg.com/profile.jpg",
                }
            ],
        )
        self.assertEqual(config.save_count, 1)

    async def test_config_avatar_failure_does_not_overwrite_old_profile(self) -> None:
        existing = {
            "group-1": {
                "display_name": "旧名",
                "username": "old_user",
                "avatar": "https://pbs.twimg.com/old.jpg",
            }
        }
        config = _runtime_config(GROUP_RENDER_OVERRIDES=existing.copy())
        service = _command_service(
            config=config,
            twitter_client=FakeTwitterClient(
                profile_error=RuntimeError("用户资料缺少 profilePicture")
            ),
        )

        with self.assertRaisesRegex(RuntimeError, "用户资料缺少 profilePicture"):
            await service.handle_xmonitor_config(
                FakeEvent("/xmonitor config avatar group-1 BrokenUser"),
                parse_xmonitor_args("/xmonitor config avatar group-1 BrokenUser"),
            )

        self.assertEqual(config["GROUP_RENDER_OVERRIDES"], existing)
        self.assertEqual(config.save_count, 0)

    async def test_config_avatar_current_requires_group_chat(self) -> None:
        service = _command_service()

        result = await service.handle_xmonitor_config(
            FakeEvent("/xmonitor config avatar current Blue_ArchiveJP"),
            parse_xmonitor_args("/xmonitor config avatar current Blue_ArchiveJP"),
        )

        self.assertEqual(result, "当前会话不是群聊，请显式传入 group_id。")

    async def test_render_failure_falls_back_to_text_and_continues(self) -> None:
        def render(tweet, options=None):
            if tweet["id"] == "bad":
                raise RuntimeError("render boom")
            return f"png-{tweet['id']}"

        sender, calls = _notification_sender(render_func=render)

        debug = await sender.notify(
            [
                _stored_tweet(_tweet("bad", "bad tweet")),
                _stored_tweet(_tweet("good", "good tweet")),
            ]
        )

        self.assertEqual(len(calls), 4)
        self.assertEqual(calls[0]["message_chain"].operations[0][0], "message")
        self.assertIn("bad tweet", calls[0]["message_chain"].operations[0][1])
        self.assertEqual(
            calls[1]["message_chain"].operations, [("message", "bad tweet")]
        )
        self.assertEqual(
            calls[2]["message_chain"].operations,
            [
                ("message", f"#{TweetHistoryStore.hash_text('good tweet')[1]}"),
                ("base64_image", "png-good"),
            ],
        )
        self.assertEqual(
            calls[3]["message_chain"].operations, [("message", "good tweet")]
        )
        self.assertEqual(debug.results[0].render_status, "error")
        self.assertEqual(debug.results[0].fallback_send.status, "success")

    async def test_image_send_failure_falls_back_per_group(self) -> None:
        sender, calls = _notification_sender(
            render_func=lambda tweet, options=None: f"png-{tweet['id']}",
            subscribe_groups=["group-1", "group-2"],
            notify_user="user-9",
            fail_image_for_groups={"group-1"},
        )

        debug = await sender.notify([_stored_tweet(_tweet("1", "fallback please"))])

        self.assertEqual(len(calls), 5)
        self.assertEqual(calls[0]["id"], "group-1")
        self.assertTrue(calls[0]["message_chain"].has_operation("base64_image"))
        self.assertEqual(calls[1]["id"], "group-1")
        self.assertTrue(calls[1]["message_chain"].has_operation("message"))
        self.assertEqual(calls[2]["id"], "group-1")
        self.assertEqual(
            calls[2]["message_chain"].operations, [("message", "fallback please")]
        )
        self.assertEqual(calls[3]["id"], "group-2")
        self.assertTrue(calls[3]["message_chain"].has_operation("base64_image"))
        self.assertEqual(calls[4]["id"], "group-2")
        self.assertEqual(
            calls[4]["message_chain"].operations, [("message", "fallback please")]
        )
        self.assertEqual(debug.results[0].image_send.status, "error")
        self.assertEqual(debug.results[0].fallback_send.status, "success")
        self.assertEqual(debug.results[0].source_send.status, "success")

    async def test_source_message_failure_is_recorded_and_does_not_raise(self) -> None:
        sender, calls = _notification_sender(
            render_func=lambda tweet, options=None: f"png-{tweet['id']}",
            fail_source_for_groups={"group-1"},
        )

        debug = await sender.notify([_stored_tweet(_tweet("1", "source breaks"))])

        self.assertEqual(len(calls), 2)
        self.assertEqual(debug.results[0].source_send.status, "error")

    def test_parse_x_command_preserves_translation_text(self) -> None:
        short_id, translation = parse_x_command(
            "/x 114514 翻译正文 https://example.test\n#测试 😀"
        )

        self.assertEqual(short_id, "114514")
        self.assertEqual(translation, "翻译正文 https://example.test\n#测试 😀")

    def test_event_group_id_accepts_method_and_message_object(self) -> None:
        self.assertEqual(event_group_id(FakeEvent(group_id="group-1")), "group-1")

        class MessageObj:
            group_id = "group-2"

        class EventWithoutMethod:
            message_obj = MessageObj()

        self.assertEqual(event_group_id(EventWithoutMethod()), "group-2")

    async def test_manual_fetch_records_history_before_reply(self) -> None:
        history_store = FakeHistoryStore()
        service = _command_service(
            history_store=history_store,
            twitter_client=FakeTwitterClient(tweets=[_tweet("1", "manual tweet")]),
        )

        result = await service.handle_manual_fetch()

        self.assertEqual(len(history_store.added), 1)
        self.assertEqual(history_store.added[0][1], "Blue_ArchiveJP")
        self.assertIn("manual tweet", result)

    async def test_history_service_records_new_and_skips_duplicates(self) -> None:
        existing_tweet = _tweet("1", "already stored")
        existing_record = FakeHistoryRecord(
            short_id=TweetHistoryStore.hash_text(existing_tweet["text"])[1],
            original_text=existing_tweet["text"],
            tweet=existing_tweet,
        )
        store = FakeHistoryStore([existing_record])
        service = HistoryService(store, account="Blue_ArchiveJP")

        stored = service.store_new_tweets([existing_tweet, _tweet("2", "new tweet")])

        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0].tweet["text"], "new tweet")
        self.assertEqual(len(store.added), 1)

    async def test_history_command_lists_latest_records(self) -> None:
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
        service = _command_service(history_store=FakeHistoryStore([second, first]))

        result = service.handle_history(limit=10)

        self.assertIn("#bbbbbb", result)
        self.assertLess(result.index("#bbbbbb"), result.index("#aaaaaa"))

    async def test_x_command_renders_original_history_tweet_as_image(self) -> None:
        captured = []

        def render(tweet, options=None):
            captured.append((tweet, options))
            return f"png-{tweet['id']}"

        record = FakeHistoryRecord(
            short_id="114514",
            original_text="original tweet",
            tweet=_tweet("1", "original tweet"),
        )
        config = _runtime_config(SOURCE_LOGO="/tmp/source-logo.png")
        service = _command_service(
            config=config,
            history_store=FakeHistoryStore([record]),
            render_func=render,
        )

        result = await service.handle_render_history("/x 114514")

        self.assertEqual(result.image_base64, "png-1")
        self.assertEqual(
            captured, [(record.tweet, {"source_logo": "/tmp/source-logo.png"})]
        )

    async def test_x_command_renders_translation_with_original_assets(self) -> None:
        captured = []

        def render(tweet, options=None):
            captured.append((tweet, options))
            return "png-translated"

        record = FakeHistoryRecord(
            short_id="114514",
            original_text="original tweet",
            tweet=_tweet("1", "original tweet"),
        )
        service = _command_service(
            history_store=FakeHistoryStore([record]),
            render_func=render,
        )

        result = await service.handle_render_history(
            "/x 114514 翻译正文 https://example.test\n#测试 😀"
        )

        self.assertEqual(result.image_base64, "png-translated")
        self.assertEqual(captured[0][0], record.tweet)
        self.assertEqual(
            captured[0][1],
            {
                "text_override": "翻译正文 https://example.test\n#测试 😀",
                "translation_style": True,
            },
        )

    async def test_x_command_returns_clear_not_found_and_collision_messages(
        self,
    ) -> None:
        history_store = FakeHistoryStore()
        service = _command_service(history_store=history_store)

        missing = await service.handle_render_history("/x 114514")

        self.assertIn("未找到 #114514", missing.text)

        history_store.lookup_error = TweetHistoryLookupCollision("短 ID 冲突")
        collided = await service.handle_render_history("/x 114514")

        self.assertEqual(collided.text, "短 ID 冲突")


if __name__ == "__main__":
    unittest.main()
