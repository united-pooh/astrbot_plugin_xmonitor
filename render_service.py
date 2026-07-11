from __future__ import annotations

import asyncio
from typing import Any, Callable

try:
    from .tweet_renderer import render_to_base64
except ImportError:  # pragma: no cover - direct test import fallback.
    from tweet_renderer import render_to_base64


RenderFunc = Callable[[dict[str, Any], dict[str, Any] | None], str | None]


class RenderService:
    def __init__(
        self,
        *,
        source_logo: str | None = None,
        render_func: RenderFunc = render_to_base64,
    ) -> None:
        self.source_logo = source_logo
        self.render_func = render_func

    def build_render_options(
        self,
        extra_options: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        render_options: dict[str, Any] = {}
        if self.source_logo:
            render_options["source_logo"] = self.source_logo
        if extra_options:
            render_options.update(extra_options)
        return render_options or None

    async def render_tweet_to_base64(
        self,
        tweet: dict[str, Any],
        extra_options: dict[str, Any] | None = None,
    ) -> str | None:
        return await asyncio.to_thread(
            self.render_func,
            tweet,
            self.build_render_options(extra_options),
        )

    async def render_history_record_to_base64(
        self,
        record: Any,
        translation_text: str | None = None,
    ) -> str | None:
        extra_options = None
        if translation_text is not None:
            extra_options = {
                "text_override": translation_text,
                "translation_style": True,
            }
        return await self.render_tweet_to_base64(record.tweet, extra_options)
