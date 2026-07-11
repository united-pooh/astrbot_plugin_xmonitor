from __future__ import annotations

from typing import Any, Callable

try:
    from .tweet_helpers import (
        build_tweet_display_lines,
        extract_tweet_image_urls,
        history_short_id_for_tweet,
        tweet_body_text,
    )
except ImportError:  # pragma: no cover - direct test import fallback.
    from tweet_helpers import (
        build_tweet_display_lines,
        extract_tweet_image_urls,
        history_short_id_for_tweet,
        tweet_body_text,
    )


MessageChainFactory = Callable[[], Any]


def default_message_chain_factory() -> Any:
    try:
        from astrbot.core.message.message_event_result import MessageChain
    except (
        ImportError
    ) as error:  # pragma: no cover - AstrBot is optional in unit tests.
        raise RuntimeError(
            "MessageChain factory is required outside AstrBot"
        ) from error
    return MessageChain()


class MessageBuilder:
    def __init__(
        self,
        *,
        target_account: str | None,
        notify_user: str | None = None,
        chain_factory: MessageChainFactory = default_message_chain_factory,
    ) -> None:
        self.target_account = target_account
        self.notify_user = notify_user
        self.chain_factory = chain_factory

    def build_notification_text(self, tweets: list[dict[str, Any]]) -> str:
        lines = [f"@{self.target_account} 有 {len(tweets)} 条新推文："]
        for index, tweet in enumerate(tweets):
            if index > 0:
                lines.append("")
            lines.extend(
                build_tweet_display_lines(
                    tweet,
                    target_account=self.target_account,
                    include_link=True,
                )
            )
        return "\n".join(lines)

    def build_text_fallback_chain(self, tweet: dict[str, Any]) -> Any:
        chain = self.chain_factory()
        if self.notify_user:
            chain = chain.at(str(self.notify_user), self.notify_user)
        return chain.message(self.build_notification_text([tweet]))

    def build_tweet_image_chain(
        self,
        *,
        short_id: str,
        image_base64: str,
    ) -> Any:
        chain = self.chain_factory()
        if self.notify_user:
            chain = chain.at(str(self.notify_user), self.notify_user)
        return chain.message(f"#{short_id}").base64_image(image_base64)

    def build_tweet_image_chain_for_tweet(
        self,
        tweet: dict[str, Any],
        image_base64: str,
    ) -> Any:
        return self.build_tweet_image_chain(
            short_id=history_short_id_for_tweet(tweet),
            image_base64=image_base64,
        )

    def build_tweet_source_chain(self, tweet: dict[str, Any]) -> Any:
        chain = self.chain_factory().message(tweet_body_text(tweet))
        for image_url in extract_tweet_image_urls(tweet):
            chain = chain.url_image(image_url)
        return chain

    def build_manual_fetch_message(self, tweets: list[dict[str, Any]]) -> str:
        lines = [f"找到 {len(tweets)} 条推文："]
        for index, tweet in enumerate(tweets):
            if index > 0:
                lines.append("")
            lines.extend(
                build_tweet_display_lines(
                    tweet,
                    target_account=self.target_account,
                    include_link=False,
                )
            )
        return "\n".join(lines)
