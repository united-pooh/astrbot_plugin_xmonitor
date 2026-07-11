# Changelog

## 2026-07-08

- Changed scheduled tweet notifications to send two messages per new tweet: `#history-short-id` plus the Pillow-rendered image, followed by the original tweet text and attached tweet images.
- Kept automatic translation scoped to the Pillow-rendered image while the second source-material message always uses the original tweet text.

## 2026-05-29

- Added per-group render identity overrides for tweet cards, including avatar, display name, and username.
- Added `/xmonitor config` commands for status, translation on/off, avatar lookup from TwitterAPI.io `profilePicture`, and manual identity overrides.
- Added optional scheduled-push text translation through a configured AstrBot Chat Provider; translation remains off by default and falls back to original text on provider errors.

## 2026-05-28

- Added local sqlite tweet history with six-character SHA-256 short IDs, `/history` listing, `/x <id>` historical image rendering, and `/x <id> <translation>` translation-body rendering that reuses original tweet assets.
- Updated Pillow rendering defaults so the top-right grok and more-action icons are hidden unless explicitly enabled, with emoji rendering covered by PNG-level tests.
- Internal complexity refactor: split `tweet_renderer.py` card image candidate extraction into focused helpers, reducing `_extract_card_image_candidates` complexity while preserving existing rendering behavior.
