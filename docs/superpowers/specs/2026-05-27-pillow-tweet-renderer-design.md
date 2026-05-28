# Pillow Tweet Renderer Design

## Objective

Render monitored X/Twitter posts as lightweight Pillow-generated images and send those images directly through AstrBot notifications. The renderer must use raw tweet JSON/text to reproduce the supplied X-style tweet detail card at screenshot-level layout fidelity while avoiding browser, HTML screenshot, or Playwright dependencies.

## Scope

- Add a dedicated Pillow rendering layer outside `main.py`.
- Generate one tweet detail card image per tweet.
- Directly integrate image sending into scheduled subscriber push.
- Keep the existing text notification as the fallback path.
- Support dynamic image height for long tweet text.
- Use a default X-detail-like 1200px-class layout with full-width body text and media aligned to the page margin, not a compact summary card.
- Support replaceable avatar and tweet media sources.
- Parse links and hashtags from the plain tweet text.
- Render URL text as `entities.urls[].display_url` when available.
- Render time, date, and view count from the tweet JSON.

## Non-Goals

- Do not launch or depend on a browser.
- Do not use AstrBot HTML-to-image helpers for the tweet layout.
- Do not implement interactive UI controls.
- Do not depend on fixed Blue Archive assets.

## Architecture

### `tweet_renderer.py`

`tweet_renderer.py` owns all Pillow-specific behavior:

- Font selection and fallback.
- Avatar/media download or local image loading.
- Rounded avatar/media masks.
- Tweet text tokenization and wrapping.
- URL underline and hashtag coloring.
- Dynamic canvas height calculation.
- Final image encoding.

The public API should expose one core image function and two output helpers:

```python
render_image(tweet: dict, options: TweetRenderOptions | None = None) -> Image.Image
render_to_base64(tweet: dict, options: TweetRenderOptions | None = None) -> str
render_to_file(tweet: dict, output_path: str | Path, options: TweetRenderOptions | None = None) -> Path
```

`render_to_base64` is the default production path. `render_to_file` exists for local preview, debugging, and tests.

### `main.py`

`main.py` remains the AstrBot orchestration layer:

- Fetch tweets through TwitterAPI.io.
- Call `render_to_base64(...)` for each new tweet.
- Send `MessageChain().base64_image(...)` to each subscribed group.
- Include configured `NOTIFY_USER` mention before the image when present.
- Fall back to the existing plain-text notification if rendering or image sending fails.

## Rendering Rules

### Layout

The output uses a fixed card width and dynamic height:

- White background.
- Left avatar column.
- Author display name and username.
- Verified and business affiliation badges beside the author name when the
  tweet JSON indicates them.
- X-style Grok and More action marks in the top-right header area.
- Rich tweet text starting from the page margin after the header.
- Optional tweet media or card image with rounded corners across the main card width.
- Optional cropped source logo on the footer row, right-aligned with media.
- Footer with local time, date, and formatted view count.

### DOM Parameter Mapping

The supplied X/Twitter DOM is treated as a 600 CSS px detail column rendered
into a 1200 image px Pillow canvas. Defaults use the following 2x mapping:

| Area | DOM/CSS signal | Pillow default | Rationale |
| --- | --- | ---: | --- |
| Canvas width | Detail column around 600 CSS px | `1200` | 2x output for crisp chat images. |
| Horizontal margin | X detail padding around 16-17 CSS px | `34` | Keeps body/media aligned to page margin. |
| Avatar size | `width: 40px; height: 40px` | `80` | Exact 2x mapping. |
| Avatar radius | `shape-square-rx-16` square avatar | `16` | Rounded square, not circular. |
| Avatar/header gap | Column gap around 10 CSS px | `20` | 2x mapping. |
| Display name | 15 CSS px bold | `30` bold | Header name stays compact but strong. |
| Username/meta | 15 CSS px regular | `30` regular | Matches X metadata scale. |
| Header action icons | Grok + More buttons at top right | `34` icon, `18` gap | Decorative marks with reserved width. |
| Verified badge | 22px SVG next to name | `26` | Simplified gold badge beside name. |
| Business badge | Small square affiliation image | `26` | Simplified black square badge. |
| Body text | Tweet text around 17 CSS px | `34` regular | 2x mapping with CJK fallback fonts. |
| Body line rhythm | Around 23-24 CSS px | font metrics + `12` padding | Produces about 47-48 px lines locally. |
| Link color | `rgb(29, 155, 240)` | `(29, 155, 240)` | X blue, underlined for URLs. |
| Hashtag color | `rgb(29, 155, 240)` | `(29, 155, 240)` | X blue, no underline. |
| Source logo | Custom plugin requirement | one footer line high | Placed on the time/views row and right-aligned with media. |
| Media top gap | About 12 CSS px | `26` | 2x spacing, no overlap with logo. |
| Media radius | About 16 CSS px | `32` | Rounded X media card corners. |
| Footer top gap | About 12 CSS px | `26` | 2x spacing after media/text. |
| Footer text | 15 CSS px metadata | `30` | Matches timestamp/views row. |
| View count weight | Number bold, label regular | split draw | Only compact number is bold. |
| Translation row | DOM may contain Show translation | never rendered | User hard requirement; options cannot enable it. |

### Text

The renderer parses rich text from `tweet["text"]` only:

- `https://...` and `http://...` tokens are links.
- `#...` tokens are hashtags until whitespace or common punctuation.
- Links are blue and underlined.
- Hashtags are blue.
- Non-link text is black.
- Newlines from the original tweet are preserved.

When a parsed URL matches `tweet["entities"]["urls"]`, the displayed link text uses `display_url`. This keeps detection based on the plain text while matching X's visible URL behavior.

### Time And Views

- Parse `createdAt` from TwitterAPI.io formats.
- Convert to Asia/Shanghai.
- Render as `上午/下午H:MM · YYYY年M月D日`.
- Format views compactly, e.g. `252967` as `252.9K 浏览`.

### Images

- Avatar source priority:
  1. Explicit render option override.
  2. `tweet["author"]["profilePicture"]`.
  3. Generated placeholder avatar.
- Tweet media source priority:
  1. Explicit render option override.
  2. Tweet media in `extendedEntities` if present.
  3. Best card image in `tweet["card"]["binding_values"]`, supporting both list and dict binding formats.
  4. No media block.

Remote images are fetched with bounded timeouts. Failures should degrade gracefully.

## Error Handling

Rendering failures must never break scheduled polling:

- Log the rendering or sending error.
- Send the existing text notification instead.
- Continue processing other tweets when possible.

If individual remote images fail, the renderer should still produce a card with placeholders or omit the affected media block.

## Dependencies

Create or update `requirements.txt` with the plugin runtime dependencies:

- `httpx`
- `APScheduler`
- `pytz`
- `Pillow`

Keep assets external or generated; do not bundle large media files.

## Tests And Validation

Add focused tests for:

- URL and hashtag tokenization from plain text.
- `display_url` replacement from tweet entities.
- Link underline metadata through pixel-level or renderer primitive assertions.
- Dynamic height growth for long tweets.
- Time conversion and view-count formatting.
- Base64 output decodes to a valid PNG.
- Optional file output writes a valid PNG.
- Push path uses base64 image by default.
- Push path falls back to text when rendering fails.
- Existing `within_time` query behavior, including `include:nativeretweets`, remains correct.

Manual validation should render a sample tweet based on the supplied JSON and confirm that the output image resembles the reference layout.

## Success Criteria

- Scheduled new-tweet notifications send Pillow-rendered tweet detail images by default.
- No browser process or HTML screenshot dependency is introduced.
- The renderer can output base64 by default and PNG files on demand.
- Long tweet text increases image height without clipping.
- Links and hashtags are styled from plain text parsing.
- Link display text uses `entities.urls[].display_url` when available.
- The default output uses a 1200px-class X detail layout with page-margin body text, large avatar/header, full-width rounded media/card image, footer-row cropped source logo, and Chinese-localized footer placement matching the reference screenshots closely.
- Tests pass with `python -m unittest discover -s tests`.
