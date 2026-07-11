from __future__ import annotations

import unittest

from tweet_translator import (
    DEFAULT_TRANSLATION_SYSTEM_PROMPT,
    build_tweet_translation_prompt,
    translate_tweet_text,
)


class FakeResponse:
    completion_text = " 翻译正文\n#测试 "


class FakeContext:
    def __init__(self) -> None:
        self.calls = []

    async def llm_generate(self, **kwargs):
        self.calls.append(kwargs)
        return FakeResponse()


class TweetTranslatorTest(unittest.IsolatedAsyncioTestCase):
    def test_build_prompt_uses_tweet_text_only(self) -> None:
        prompt = build_tweet_translation_prompt(
            {
                "text": "原文\n#测试",
                "media": [{"url": "https://example.test/image.jpg"}],
            }
        )

        self.assertIn("原文\n#测试", prompt)
        self.assertNotIn("image.jpg", prompt)

    async def test_translate_tweet_text_calls_llm_without_image_context(self) -> None:
        context = FakeContext()

        translated = await translate_tweet_text(
            context,
            chat_provider_id="provider-1",
            tweet={"text": "原文"},
            system_prompt="系统提示",
        )

        self.assertEqual(translated, "翻译正文\n#测试")
        self.assertEqual(
            context.calls,
            [
                {
                    "chat_provider_id": "provider-1",
                    "prompt": "请翻译下面这条推文正文：\n\n原文",
                    "system_prompt": "系统提示",
                }
            ],
        )

    async def test_translate_tweet_text_uses_default_prompt_and_rejects_empty_result(
        self,
    ) -> None:
        class EmptyResponse:
            completion_text = "   "

        class EmptyContext(FakeContext):
            async def llm_generate(self, **kwargs):
                self.calls.append(kwargs)
                return EmptyResponse()

        context = EmptyContext()

        translated = await translate_tweet_text(
            context,
            chat_provider_id="provider-1",
            tweet={"text": "原文"},
        )

        self.assertIsNone(translated)
        self.assertEqual(
            context.calls[0]["system_prompt"], DEFAULT_TRANSLATION_SYSTEM_PROMPT
        )


if __name__ == "__main__":
    unittest.main()
