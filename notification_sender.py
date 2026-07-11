from __future__ import annotations

from typing import Any, Awaitable, Callable

try:
    from .history_service import StoredTweet
    from .message_builder import MessageBuilder
    from .notification_plan import (
        MessageSendDebug,
        NotificationBatchDebug,
        NotificationDebugResult,
    )
    from .render_service import RenderService
    from .translation_service import TranslationResult, TranslationService
    from .tweet_helpers import extract_tweet_id
except ImportError:  # pragma: no cover - direct test import fallback.
    from history_service import StoredTweet
    from message_builder import MessageBuilder
    from notification_plan import (
        MessageSendDebug,
        NotificationBatchDebug,
        NotificationDebugResult,
    )
    from render_service import RenderService
    from translation_service import TranslationResult, TranslationService
    from tweet_helpers import extract_tweet_id


SendMessageCallable = Callable[..., Awaitable[Any]]
RenderOptionsProvider = Callable[[str], dict[str, Any] | None]


class NotificationSender:
    def __init__(
        self,
        *,
        subscribe_groups: list[str],
        target_account: str | None,
        render_service: RenderService,
        translation_service: TranslationService,
        message_builder: MessageBuilder,
        send_message: SendMessageCallable,
        render_options_for_group: RenderOptionsProvider | None = None,
        logger: Any | None = None,
    ) -> None:
        self.subscribe_groups = subscribe_groups
        self.target_account = target_account
        self.render_service = render_service
        self.translation_service = translation_service
        self.message_builder = message_builder
        self.send_message = send_message
        self.render_options_for_group = render_options_for_group or (
            lambda _group: None
        )
        self.logger = logger

    async def notify(
        self,
        items: list[StoredTweet],
    ) -> NotificationBatchDebug:
        if not items:
            return NotificationBatchDebug(skipped_reason="no_items")
        if not self.subscribe_groups:
            self._warning("未配置 SUBSCRIBE_GROUPS，跳过主动推送。")
            return NotificationBatchDebug(skipped_reason="no_subscribe_groups")

        translations = await self.translation_service.build_translation_overrides(items)
        results: list[NotificationDebugResult] = []
        for group_id in self.subscribe_groups:
            for index, stored_tweet in enumerate(items):
                result = await self._notify_one(
                    group_id=group_id,
                    stored_tweet=stored_tweet,
                    translation=translations[index],
                )
                results.append(result)
        return NotificationBatchDebug(results=results)

    async def _notify_one(
        self,
        *,
        group_id: str,
        stored_tweet: StoredTweet,
        translation: TranslationResult,
    ) -> NotificationDebugResult:
        tweet = stored_tweet.tweet
        short_id = str(getattr(stored_tweet.record, "short_id"))
        tweet_id = extract_tweet_id(tweet)

        render_options = dict(self.render_options_for_group(group_id) or {})
        if translation.status == "success" and translation.text:
            render_options.update(
                {
                    "text_override": translation.text,
                    "translation_style": True,
                }
            )

        render_status = "pending"
        render_error: str | None = None
        image_send = MessageSendDebug(kind="image", status="not_attempted")
        fallback_send: MessageSendDebug | None = None

        try:
            image_base64 = await self.render_service.render_tweet_to_base64(
                tweet,
                render_options or None,
            )
        except Exception as error:
            render_status = "error"
            render_error = str(error)
            self._error(
                f"渲染 @{self.target_account} 新推文图片失败，改用纯文本: {error}"
            )
            fallback_send = await self._send_text_fallback(group_id, tweet, error)
        else:
            if not image_base64:
                render_status = "empty"
                render_error = "推文图片渲染结果为空"
                fallback_send = await self._send_text_fallback(
                    group_id,
                    tweet,
                    RuntimeError(render_error),
                )
            else:
                render_status = "success"
                image_send = await self._send_image_message(
                    group_id,
                    short_id,
                    image_base64,
                    tweet,
                )
                if image_send.status == "error":
                    fallback_send = await self._send_text_fallback(
                        group_id,
                        tweet,
                        RuntimeError(image_send.error or "image send failed"),
                    )

        source_send = await self._send_source_message(group_id, tweet)
        return NotificationDebugResult(
            group_id=group_id,
            short_id=short_id,
            tweet_id=tweet_id,
            translation_status=translation.status,
            render_status=render_status,
            image_send=image_send,
            fallback_send=fallback_send,
            source_send=source_send,
            translation_error=translation.error,
            render_error=render_error,
        )

    async def _send_image_message(
        self,
        group_id: str,
        short_id: str,
        image_base64: str,
        tweet: dict[str, Any],
    ) -> MessageSendDebug:
        try:
            chain = self.message_builder.build_tweet_image_chain(
                short_id=short_id,
                image_base64=image_base64,
            )
            await self.send_message(
                type="GroupMessage",
                id=group_id,
                message_chain=chain,
            )
            self._info(f"向群组 {group_id} 推送 @{self.target_account} 新推文图片成功")
            return MessageSendDebug(kind="image", status="success")
        except Exception as error:
            self._error(
                f"向群组 {group_id} 推送 @{self.target_account} 新推文图片失败: {error}"
            )
            return MessageSendDebug(kind="image", status="error", error=str(error))

    async def _send_text_fallback(
        self,
        group_id: str,
        tweet: dict[str, Any],
        reason: Exception,
    ) -> MessageSendDebug:
        try:
            await self.send_message(
                type="GroupMessage",
                id=group_id,
                message_chain=self.message_builder.build_text_fallback_chain(tweet),
            )
            self._info(
                f"已向群组 {group_id} 发送 @{self.target_account} 新推文纯文本 fallback"
            )
            return MessageSendDebug(kind="text_fallback", status="success")
        except Exception as error:
            self._error(
                f"向群组 {group_id} 发送 @{self.target_account} 纯文本 fallback 失败: "
                f"{error}; 原始错误: {reason}"
            )
            return MessageSendDebug(
                kind="text_fallback",
                status="error",
                error=str(error),
            )

    async def _send_source_message(
        self,
        group_id: str,
        tweet: dict[str, Any],
    ) -> MessageSendDebug:
        try:
            await self.send_message(
                type="GroupMessage",
                id=group_id,
                message_chain=self.message_builder.build_tweet_source_chain(tweet),
            )
            self._info(
                f"向群组 {group_id} 推送 @{self.target_account} 新推文原文素材成功"
            )
            return MessageSendDebug(kind="source", status="success")
        except Exception as error:
            tweet_id = extract_tweet_id(tweet) or "unknown"
            self._error(
                f"向群组 {group_id} 推送 @{self.target_account} 新推文原文素材失败 "
                f"tweet_id={tweet_id}: {error}"
            )
            return MessageSendDebug(kind="source", status="error", error=str(error))

    def _info(self, message: str) -> None:
        if self.logger is not None:
            self.logger.info(message)

    def _warning(self, message: str) -> None:
        if self.logger is not None:
            self.logger.warning(message)

    def _error(self, message: str) -> None:
        if self.logger is not None:
            self.logger.error(message)
