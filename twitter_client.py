from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

try:
    from .config import normalize_optional_text
    from .tweet_helpers import (
        build_search_query,
        extract_tweet_id,
        parse_tweet_datetime,
    )
except ImportError:  # pragma: no cover - direct test import fallback.
    from config import normalize_optional_text
    from tweet_helpers import build_search_query, extract_tweet_id, parse_tweet_datetime


class TwitterClient:
    TWITTER_SEARCH_URL = "https://api.twitterapi.io/twitter/tweet/advanced_search"
    TWITTER_USER_INFO_URL = "https://api.twitterapi.io/twitter/user/info"

    def __init__(
        self,
        *,
        api_key: str,
        target_account: str | None,
        check_interval_minutes: int,
        httpx_module: Any = httpx,
        search_url: str = TWITTER_SEARCH_URL,
        user_info_url: str = TWITTER_USER_INFO_URL,
    ) -> None:
        self.api_key = api_key
        self.target_account = target_account
        self.check_interval_minutes = check_interval_minutes
        self.httpx = httpx_module
        self.search_url = search_url
        self.user_info_url = user_info_url

    def build_search_query(self) -> str:
        return build_search_query(self.target_account, self.check_interval_minutes)

    @staticmethod
    def dedupe_tweets(
        tweets: list[dict[str, Any]],
        seen_ids: set[str],
    ) -> list[dict[str, Any]]:
        unique_tweets: list[dict[str, Any]] = []
        for tweet in tweets:
            tweet_id = extract_tweet_id(tweet)
            if tweet_id and tweet_id in seen_ids:
                continue
            if tweet_id:
                seen_ids.add(tweet_id)
            unique_tweets.append(tweet)
        return unique_tweets

    async def fetch_search_window(
        self,
        client: Any,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        query = self.build_search_query()
        params = {"query": query, "queryType": "Latest"}
        if cursor:
            params["cursor"] = cursor
        response = await client.get(
            self.search_url,
            headers={"X-API-Key": self.api_key},
            params=params,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("status") == "error":
            message = data.get("message") or data.get("msg") or "unknown error"
            raise RuntimeError(f"TwitterAPI.io advanced_search 返回错误: {message}")
        return data

    async def fetch_recent_tweets(self) -> list[dict[str, Any]]:
        if not self.api_key:
            raise RuntimeError("缺少 TwitterAPI.io API key")

        seen_ids: set[str] = set()
        tweets: list[dict[str, Any]] = []
        next_cursor = None

        async with self.httpx.AsyncClient(timeout=30) as client:
            while True:
                data = await self.fetch_search_window(client, next_cursor)
                window_tweets = data.get("tweets", []) or []
                tweets.extend(self.dedupe_tweets(window_tweets, seen_ids))

                next_cursor = data.get("next_cursor")
                if not (data.get("has_next_page") and next_cursor):
                    break

        tweets.sort(
            key=lambda item: parse_tweet_datetime(item)
            or datetime.min.replace(tzinfo=timezone.utc)
        )
        return tweets

    async def fetch_user_profile(self, user_name: str) -> dict[str, Any]:
        normalized_user = normalize_optional_text(user_name)
        if normalized_user is None:
            raise ValueError("缺少 userName")
        if not self.api_key:
            raise RuntimeError("缺少 TwitterAPI.io API key")

        async with self.httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                self.user_info_url,
                headers={"X-API-Key": self.api_key},
                params={"userName": normalized_user.lstrip("@")},
            )
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") == "error":
            message = payload.get("message") or payload.get("msg") or "unknown error"
            raise RuntimeError(f"TwitterAPI.io user/info 返回错误: {message}")

        profile = payload.get("data") or {}
        if not isinstance(profile, dict):
            raise RuntimeError("TwitterAPI.io user/info 响应缺少 data")
        if normalize_optional_text(profile.get("profilePicture")) is None:
            raise RuntimeError("用户资料缺少 profilePicture")
        return profile
