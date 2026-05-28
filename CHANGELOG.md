# Changelog

## 2026-05-28

- Added local sqlite tweet history with six-character SHA-256 short IDs, `/history` listing, `/x <id>` historical image rendering, and `/x <id> <translation>` translation-body rendering that reuses original tweet assets.
- Updated Pillow rendering defaults so the top-right grok and more-action icons are hidden unless explicitly enabled, with emoji rendering covered by PNG-level tests.
- Internal complexity refactor: split `tweet_renderer.py` card image candidate extraction into focused helpers, reducing `_extract_card_image_candidates` complexity while preserving existing rendering behavior.
