from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Awaitable

try:
    from .history_service import StoredTweet
    from .tweet_helpers import extract_tweet_id
    from .tweet_translator import (
        DEFAULT_TRANSLATION_SYSTEM_PROMPT,
        translate_tweet_text,
    )
except ImportError:  # pragma: no cover - direct test import fallback.
    from history_service import StoredTweet
    from tweet_helpers import extract_tweet_id
    from tweet_translator import DEFAULT_TRANSLATION_SYSTEM_PROMPT, translate_tweet_text


TranslateFunc = Callable[..., Awaitable[str | None]]


@dataclass(frozen=True)
class TranslationResult:
    status: str
    text: str | None = None
    error: str | None = None


class TranslationService:
    def __init__(
        self,
        *,
        context: Any,
        enabled: bool,
        provider_id: str | None,
        system_prompt: str | None = None,
        logger: Any | None = None,
        translate_func: TranslateFunc = translate_tweet_text,
    ) -> None:
        self.context = context
        self.enabled = enabled
        self.provider_id = provider_id
        self.system_prompt = system_prompt or DEFAULT_TRANSLATION_SYSTEM_PROMPT
        self.logger = logger
        self.translate_func = translate_func

    async def translate_tweet(self, tweet: dict[str, Any]) -> TranslationResult:
        if not self.enabled:
            return TranslationResult(status="disabled")
        if not self.provider_id:
            self._warning(
                "ENABLE_AUTO_TRANSLATION 已开启，但未配置 TRANSLATION_PROVIDER_ID。"
            )
            return TranslationResult(status="skipped_no_provider")

        try:
            translated_text = await self.translate_func(
                self.context,
                chat_provider_id=self.provider_id,
                tweet=tweet,
                system_prompt=self.system_prompt,
            )
        except Exception as error:
            tweet_id = extract_tweet_id(tweet) or "unknown"
            self._error(f"自动翻译推文失败 tweet_id={tweet_id}: {error}")
            return TranslationResult(status="error", error=str(error))

        if not translated_text:
            return TranslationResult(status="empty")
        return TranslationResult(status="success", text=translated_text)

    async def build_translation_overrides(
        self,
        stored_tweets: list[StoredTweet],
    ) -> list[TranslationResult]:
        return [await self.translate_tweet(item.tweet) for item in stored_tweets]

    def _warning(self, message: str) -> None:
        if self.logger is not None:
            self.logger.warning(message)

    def _error(self, message: str) -> None:
        if self.logger is not None:
            self.logger.error(message)
