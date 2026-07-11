from __future__ import annotations

from typing import Any


DEFAULT_TRANSLATION_SYSTEM_PROMPT = (
    "你是一个推文翻译助手。请把用户提供的 X/Twitter 推文正文翻译成简体中文，"
    "保留链接、话题标签、换行和 emoji，只输出译文正文。"
)


def build_tweet_translation_prompt(tweet: dict[str, Any]) -> str:
    text = str(tweet.get("text") or "").strip()
    return f"请翻译下面这条推文正文：\n\n{text}"


async def translate_tweet_text(
    context: Any,
    *,
    chat_provider_id: str,
    tweet: dict[str, Any],
    system_prompt: str | None = None,
) -> str | None:
    prompt = build_tweet_translation_prompt(tweet)
    response = await context.llm_generate(
        chat_provider_id=chat_provider_id,
        prompt=prompt,
        system_prompt=system_prompt or DEFAULT_TRANSLATION_SYSTEM_PROMPT,
    )
    text = str(getattr(response, "completion_text", "") or "").strip()
    return text or None
