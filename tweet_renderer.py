from __future__ import annotations

import base64
import ipaddress
import io
import re
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps

Font = Any

BLUE = (29, 155, 240)
BLACK = (15, 20, 25)
GRAY = (83, 100, 113)
LIGHT_GRAY = (239, 243, 244)
WHITE = (255, 255, 255)
URL_RE = re.compile(r"https?://[^\s]+")
HASHTAG_RE = re.compile(r"#[^\s#.,!?;:()\[\]{}<>\"']+")
TRAILING_URL_PUNCTUATION = ".,!?;:)]}\"'"

DEFAULT_WIDTH = 1200
DEFAULT_MARGIN = 34
DEFAULT_AVATAR_SIZE = 80
DEFAULT_AVATAR_RADIUS = 16
DEFAULT_CONTENT_GAP = 20
DEFAULT_MEDIA_RADIUS = 32
DEFAULT_BODY_FONT_SIZE = 34
DEFAULT_HEADER_FONT_SIZE = 30
DEFAULT_META_FONT_SIZE = 30
DEFAULT_FOOTER_FONT_SIZE = 30
DEFAULT_BODY_LINE_PADDING = 12
DEFAULT_TEXT_HEADER_GAP = 28
DEFAULT_MEDIA_MARGIN_TOP = 26
DEFAULT_FOOTER_MARGIN_TOP = 26
DEFAULT_SOURCE_LOGO_MARGIN_TOP = 8
DEFAULT_SOURCE_LOGO_POSITION = "footer"
DEFAULT_SHOW_HEADER_ACTIONS = False
DEFAULT_ACTION_ICON_SIZE = 34
DEFAULT_ACTION_ICON_GAP = 18
DEFAULT_ACTION_ICON_TOP_OFFSET = 4
DEFAULT_VERIFIED_BADGE_SIZE = 26
DEFAULT_BADGE_GAP = 6
GOLD = (226, 183, 25)
DARK_BADGE = (15, 20, 25)
TEXT_GLYPH_COMPATIBILITY_MAP = str.maketrans(
    {
        "\u301c": "\uff5e",
    }
)
CJK_FONT_SAMPLE = "汉語あ维護翻訳"
MISSING_CJK_FONT_MESSAGE = (
    "未找到可渲染中日韩字符的字体。请安装 Noto Sans CJK/Source Han Sans/"
    "WenQuanYi 等字体，或在插件配置 FONT_PATHS 中指定字体文件路径。"
)


@dataclass(frozen=True)
class TextSegment:
    text: str
    kind: str = "text"
    fill: tuple[int, int, int] = BLACK
    underline: bool = False


def render_image(
    tweet: dict[str, Any], options: dict[str, Any] | None = None
) -> Image.Image:
    """Render a single tweet JSON object to a Pillow image."""
    options = dict(options or {})
    width = int(options.get("width", DEFAULT_WIDTH))
    margin = int(options.get("margin", DEFAULT_MARGIN))
    avatar_size = int(options.get("avatar_size", DEFAULT_AVATAR_SIZE))
    avatar_radius = int(options.get("avatar_radius", DEFAULT_AVATAR_RADIUS))
    media_radius = int(options.get("media_radius", DEFAULT_MEDIA_RADIUS))
    content_gap = int(options.get("content_gap", DEFAULT_CONTENT_GAP))
    action_zone_width = _header_action_zone_width(options)
    header_x = margin + avatar_size + content_gap
    header_width = width - header_x - margin - action_zone_width
    main_x = margin
    main_width = width - (2 * margin)

    fonts = _load_fonts(options)
    measuring_image = Image.new("RGB", (width, 10), WHITE)
    draw = ImageDraw.Draw(measuring_image)

    avatar = _load_avatar(tweet, options, avatar_size)
    media = _load_media(tweet, options)
    source_logo = _load_source_logo(tweet, options)
    source_logo_position = _source_logo_position(options)
    body_tweet = _tweet_for_body_text(tweet, options)
    text_segments = _parse_rich_text(body_tweet)
    text_lines = _wrap_segments(
        draw,
        text_segments,
        fonts["body"],
        main_width,
        emoji_font=fonts.get("emoji"),
    )

    line_height = _line_height(
        fonts["body"], int(options.get("body_line_padding", DEFAULT_BODY_LINE_PADDING))
    )
    text_height = max(1, len(text_lines)) * line_height
    footer_height = _line_height(fonts["footer"], 8)
    source_logo_size: tuple[int, int] | None = None
    if source_logo is not None:
        default_logo_height = (
            footer_height if source_logo_position == "footer" else line_height
        )
        logo_height = int(options.get("source_logo_height") or default_logo_height)
        logo_height = max(1, logo_height)
        logo_width = min(
            main_width,
            max(1, round(source_logo.width * logo_height / source_logo.height)),
        )
        source_logo_size = (logo_width, logo_height)

    media_height = 0
    media_size: tuple[int, int] | None = None
    if media is not None:
        media_width = main_width
        media_height = int(
            options.get("media_height") or _scaled_height(media, media_width)
        )
        media_size = (media_width, media_height)

    header_height = max(
        avatar_size,
        _line_height(fonts["bold"], 4) + _line_height(fonts["meta"], 2),
    )

    y = margin
    text_y = y + header_height + int(
        options.get("text_header_gap", DEFAULT_TEXT_HEADER_GAP)
    )

    cursor_y = text_y + text_height
    source_logo_y: int | None = None
    if source_logo_size is not None and source_logo_position == "body":
        cursor_y += int(
            options.get("source_logo_margin_top", DEFAULT_SOURCE_LOGO_MARGIN_TOP)
        )
        source_logo_y = cursor_y
        cursor_y += source_logo_size[1]

    media_y: int | None = None
    if media_size is not None:
        cursor_y += int(options.get("media_margin_top", DEFAULT_MEDIA_MARGIN_TOP))
        media_y = cursor_y
        cursor_y += media_height

    footer_y = cursor_y + int(
        options.get("footer_margin_top", DEFAULT_FOOTER_MARGIN_TOP)
    )
    height = footer_y + footer_height + margin
    height = max(height, 240)

    image = Image.new("RGB", (width, height), WHITE)
    draw = ImageDraw.Draw(image)

    _draw_avatar(image, avatar, (margin, y), avatar_size, radius=avatar_radius)
    _draw_header(draw, tweet, (header_x, y), fonts, header_width, options)
    _draw_header_actions(draw, width, margin, y, options)
    _draw_rich_lines(
        draw,
        text_lines,
        (main_x, text_y),
        fonts["body"],
        line_height,
        emoji_font=fonts.get("emoji"),
    )

    if (
        source_logo is not None
        and source_logo_size is not None
        and source_logo_y is not None
    ):
        _draw_image(image, source_logo, (main_x, source_logo_y), source_logo_size)

    if media is not None and media_size is not None and media_y is not None:
        _draw_rounded_image(
            image, media, (main_x, media_y), media_size, radius=media_radius
        )

    _draw_footer(draw, tweet, (main_x, footer_y), fonts)
    if (
        source_logo is not None
        and source_logo_size is not None
        and source_logo_position == "footer"
    ):
        _draw_footer_logo(
            image,
            draw,
            source_logo,
            source_logo_size,
            (main_x, footer_y),
            fonts,
            main_width,
        )
    return image


def render_to_base64(
    tweet: dict[str, Any], options: dict[str, Any] | None = None
) -> str:
    image = render_image(tweet, options)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return base64.b64encode(output.getvalue()).decode("ascii")


def render_to_file(
    tweet: dict[str, Any],
    output_path: str | Path,
    options: dict[str, Any] | None = None,
) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    image = render_image(tweet, options)
    image.save(path, format="PNG")
    return path


def _load_fonts(options: dict[str, Any]) -> dict[str, Font]:
    font_paths = list(options.get("font_paths") or [])
    font_paths.extend(
        [
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
            "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
            "/System/Library/Fonts/ヒラギノ角ゴシック W4.ttc",
            "/System/Library/Fonts/PingFang.ttc",
            "/Library/Fonts/Arial Unicode.ttf",
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
            "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Regular.otf",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/source-han-sans/SourceHanSans-Regular.ttc",
            "/usr/share/fonts/opentype/source-han-sans/SourceHanSansSC-Regular.otf",
            "/usr/share/fonts/opentype/source-han-sans/SourceHanSansCN-Regular.otf",
            "/usr/share/fonts/adobe-source-han-sans/SourceHanSansSC-Regular.otf",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
            "/usr/share/fonts/truetype/arphic/uming.ttc",
            "/usr/share/fonts/truetype/arphic/ukai.ttc",
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/msgothic.ttc",
        ]
    )
    bold_font_paths = list(options.get("bold_font_paths") or [])
    bold_font_paths.extend(
        [
            ("/System/Library/Fonts/Hiragino Sans GB.ttc", 2),
            "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
            "/System/Library/Fonts/ヒラギノ角ゴシック W7.ttc",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/Library/Fonts/Arial Bold.ttf",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Bold.otf",
            "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Bold.otf",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/opentype/source-han-sans/SourceHanSans-Bold.ttc",
            "/usr/share/fonts/opentype/source-han-sans/SourceHanSansSC-Bold.otf",
            "/usr/share/fonts/opentype/source-han-sans/SourceHanSansCN-Bold.otf",
            "/usr/share/fonts/adobe-source-han-sans/SourceHanSansSC-Bold.otf",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
            "C:/Windows/Fonts/msyhbd.ttc",
            "C:/Windows/Fonts/msgothic.ttc",
        ]
    )
    return {
        "body": _load_font(
            font_paths, int(options.get("body_font_size", DEFAULT_BODY_FONT_SIZE))
        ),
        "bold": _load_font(
            bold_font_paths + font_paths,
            int(options.get("bold_font_size", DEFAULT_HEADER_FONT_SIZE)),
        ),
        "meta": _load_font(
            font_paths, int(options.get("meta_font_size", DEFAULT_META_FONT_SIZE))
        ),
        "source": _load_font(
            font_paths, int(options.get("source_font_size", DEFAULT_META_FONT_SIZE))
        ),
        "footer": _load_font(
            font_paths, int(options.get("footer_font_size", DEFAULT_FOOTER_FONT_SIZE))
        ),
        "footer_bold": _load_font(
            bold_font_paths + font_paths,
            int(options.get("footer_font_size", DEFAULT_FOOTER_FONT_SIZE)),
        ),
        "emoji": _load_emoji_font(
            int(options.get("body_font_size", DEFAULT_BODY_FONT_SIZE)),
            options,
        ),
    }


def _load_emoji_font(size: int, options: dict[str, Any]) -> Font | None:
    emoji_font_paths = list(options.get("emoji_font_paths") or [])
    emoji_font_paths.extend(
        [
            "/System/Library/Fonts/Apple Color Emoji.ttc",
            "/System/Library/Fonts/Apple Symbols.ttf",
            "/System/Library/Fonts/CJKSymbolsFallback.ttc",
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
            "/Library/Fonts/Arial Unicode.ttf",
        ]
    )
    candidate_sizes = [
        size,
        max(size + 1, round(size * 1.18)),
        40,
        48,
        64,
        96,
        109,
        128,
        136,
        160,
    ]
    for source in emoji_font_paths:
        path = str(source)
        if not path or not Path(path).exists():
            continue
        for candidate_size in candidate_sizes:
            try:
                return ImageFont.truetype(path, size=int(candidate_size))
            except OSError:
                continue
    return None


def _load_font(
    font_paths: list[Any],
    size: int,
    *,
    require_cjk: bool = True,
) -> Font:
    for source in font_paths:
        index = 0
        if isinstance(source, (list, tuple)):
            if not source:
                continue
            path = str(source[0])
            if len(source) > 1:
                try:
                    index = int(source[1])
                except (TypeError, ValueError):
                    index = 0
        else:
            path = str(source)
        try:
            if path and Path(path).exists():
                font = ImageFont.truetype(path, size=size, index=index)
                if not require_cjk or _font_supports_cjk(font):
                    return font
        except OSError:
            continue
    try:
        font = ImageFont.truetype("Arial.ttf", size=size)
        if not require_cjk or _font_supports_cjk(font):
            return font
    except OSError:
        pass
    default_font = ImageFont.load_default()
    if not require_cjk or _font_supports_cjk(default_font):
        return default_font
    raise RuntimeError(MISSING_CJK_FONT_MESSAGE)


def _font_supports_cjk(font: Font) -> bool:
    signatures: set[tuple[tuple[int, int, int, int], bytes]] = set()
    for character in CJK_FONT_SAMPLE:
        signature = _glyph_bitmap_signature(font, character)
        if signature is not None:
            signatures.add(signature)
    return len(signatures) >= 5


def _glyph_bitmap_signature(
    font: Font,
    character: str,
) -> tuple[tuple[int, int, int, int], bytes] | None:
    image = Image.new("L", (96, 96), 0)
    draw = ImageDraw.Draw(image)
    try:
        draw.text((8, 8), character, font=font, fill=255)
    except (OSError, UnicodeEncodeError):
        return None
    bbox = image.getbbox()
    if bbox is None:
        return None
    return bbox, image.crop(bbox).tobytes()


def _line_height(font: Font, padding: int) -> int:
    ascent, descent = _font_metrics(font)
    return max(1, ascent + descent + padding)


def _font_metrics(font: Font) -> tuple[int, int]:
    try:
        ascent, descent = font.getmetrics()
        return max(1, int(ascent)), max(0, int(descent))
    except AttributeError:
        bbox = font.getbbox("Ag国")
        return max(1, bbox[3] - bbox[1]), 0


def _line_baseline(font: Font, line_top: int, line_height: int) -> int:
    ascent, descent = _font_metrics(font)
    text_box_height = ascent + descent
    return round(line_top + max(0, line_height - text_box_height) / 2 + ascent)


def _draw_header(
    draw: ImageDraw.ImageDraw,
    tweet: dict[str, Any],
    xy: tuple[int, int],
    fonts: dict[str, Font],
    width: int,
    options: dict[str, Any],
) -> None:
    author = _author(tweet)
    name = str(
        author.get("name")
        or author.get("displayName")
        or author.get("userName")
        or "Unknown"
    )
    username = str(
        author.get("userName")
        or author.get("username")
        or author.get("screen_name")
        or ""
    )
    if username and not username.startswith("@"):
        username = f"@{username}"

    x, y = xy
    verified = _author_is_verified(author)
    affiliate = _author_has_affiliate_badge(author)
    badge_size = int(options.get("verified_badge_size", DEFAULT_VERIFIED_BADGE_SIZE))
    badge_gap = int(options.get("badge_gap", DEFAULT_BADGE_GAP))
    badge_count = int(verified) + int(affiliate)
    badge_width = 0
    if badge_count:
        badge_width = badge_count * badge_size + badge_count * badge_gap

    name = _ellipsize(draw, name, fonts["bold"], max(1, width - badge_width))
    draw.text((x, y), name, font=fonts["bold"], fill=BLACK)
    badge_x = x + draw.textlength(name, font=fonts["bold"]) + badge_gap
    badge_y = y + max(0, (_line_height(fonts["bold"], 2) - badge_size) // 2)
    if verified:
        _draw_verified_badge(draw, (int(badge_x), int(badge_y)), badge_size)
        badge_x += badge_size + badge_gap
    if affiliate:
        _draw_affiliate_badge(draw, (int(badge_x), int(badge_y)), badge_size)

    if username:
        draw.text(
            (x, y + _line_height(fonts["bold"], 2)),
            username,
            font=fonts["meta"],
            fill=GRAY,
        )


def _header_action_zone_width(options: dict[str, Any]) -> int:
    if not _show_header_actions(options):
        return 0
    size = int(options.get("action_icon_size", DEFAULT_ACTION_ICON_SIZE))
    gap = int(options.get("action_icon_gap", DEFAULT_ACTION_ICON_GAP))
    return (size * 2) + gap + 8


def _draw_header_actions(
    draw: ImageDraw.ImageDraw,
    width: int,
    margin: int,
    y: int,
    options: dict[str, Any],
) -> None:
    if not _show_header_actions(options):
        return

    size = int(options.get("action_icon_size", DEFAULT_ACTION_ICON_SIZE))
    gap = int(options.get("action_icon_gap", DEFAULT_ACTION_ICON_GAP))
    top = y + int(options.get("action_icon_top_offset", DEFAULT_ACTION_ICON_TOP_OFFSET))
    right = width - margin
    more_x = right - size
    grok_x = more_x - gap - size

    _draw_grok_icon(draw, (grok_x, top), size)
    _draw_more_icon(draw, (more_x, top), size)


def _show_header_actions(options: dict[str, Any]) -> bool:
    return bool(options.get("show_header_actions", DEFAULT_SHOW_HEADER_ACTIONS))


def _draw_grok_icon(
    draw: ImageDraw.ImageDraw, xy: tuple[int, int], size: int
) -> None:
    x, y = xy
    box = (x + 3, y + 3, x + size - 3, y + size - 3)
    draw.arc(box, start=35, end=315, fill=BLACK, width=4)
    draw.line(
        (x + size - 3, y + 2, x + 2, y + size - 3),
        fill=BLACK,
        width=4,
    )


def _draw_more_icon(
    draw: ImageDraw.ImageDraw, xy: tuple[int, int], size: int
) -> None:
    x, y = xy
    cy = y + size // 2
    radius = max(2, size // 11)
    spacing = max(radius * 3, size // 4)
    start_x = x + (size - (spacing * 2)) // 2
    for index in range(3):
        cx = start_x + index * spacing
        draw.ellipse(
            (cx - radius, cy - radius, cx + radius, cy + radius),
            fill=GRAY,
        )


def _draw_verified_badge(
    draw: ImageDraw.ImageDraw, xy: tuple[int, int], size: int
) -> None:
    x, y = xy
    draw.ellipse((x, y, x + size, y + size), fill=GOLD)
    draw.line(
        (
            x + round(size * 0.27),
            y + round(size * 0.52),
            x + round(size * 0.43),
            y + round(size * 0.68),
            x + round(size * 0.75),
            y + round(size * 0.33),
        ),
        fill=WHITE,
        width=max(2, size // 8),
        joint="curve",
    )


def _draw_affiliate_badge(
    draw: ImageDraw.ImageDraw, xy: tuple[int, int], size: int
) -> None:
    x, y = xy
    radius = max(3, size // 7)
    draw.rounded_rectangle((x, y, x + size, y + size), radius=radius, fill=DARK_BADGE)
    inset = max(5, size // 4)
    draw.rectangle(
        (x + inset, y + inset, x + size - inset, y + size - inset),
        outline=WHITE,
        width=max(1, size // 12),
    )
    draw.line(
        (x + inset, y + size // 2, x + size - inset, y + size // 2),
        fill=WHITE,
        width=max(1, size // 12),
    )


def _author_is_verified(author: dict[str, Any]) -> bool:
    return any(
        bool(author.get(key))
        for key in ("isVerified", "isBlueVerified", "verified", "blueVerified")
    )


def _author_has_affiliate_badge(author: dict[str, Any]) -> bool:
    verified_type = str(author.get("verifiedType") or "").strip().lower()
    if verified_type in {"business", "government"}:
        return True
    label = author.get("affiliatesHighlightedLabel") or author.get(
        "affiliates_highlighted_label"
    )
    return bool(label)


def _draw_rich_lines(
    draw: ImageDraw.ImageDraw,
    lines: list[list[TextSegment]],
    xy: tuple[int, int],
    font: Font,
    line_height: int,
    *,
    emoji_font: Font | None = None,
) -> None:
    x0, y = xy
    for line in lines:
        x = float(x0)
        baseline_y = _line_baseline(font, y, line_height)
        for segment in line:
            for run_text, run_font, embedded_color in _iter_font_runs(
                segment.text,
                font,
                emoji_font,
            ):
                run_baseline_y = _inline_run_baseline_y(
                    draw,
                    run_text,
                    run_font,
                    y,
                    line_height,
                    baseline_y,
                    embedded_color=embedded_color,
                )
                if embedded_color:
                    draw.text(
                        (x, run_baseline_y),
                        run_text,
                        font=run_font,
                        fill=segment.fill,
                        anchor="ls",
                        embedded_color=True,
                    )
                else:
                    draw.text(
                        (x, run_baseline_y),
                        run_text,
                        font=run_font,
                        fill=segment.fill,
                        anchor="ls",
                )
                text_width = draw.textlength(run_text, font=run_font)
                if segment.underline and run_text:
                    underline_y = min(y + line_height - 2, run_baseline_y + 3)
                    draw.line(
                        (x, underline_y, x + text_width, underline_y),
                        fill=segment.fill,
                        width=1,
                    )
                x += text_width
        y += line_height


def _inline_run_baseline_y(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: Font,
    line_top: int,
    line_height: int,
    default_baseline_y: int,
    *,
    embedded_color: bool,
) -> int:
    if not embedded_color or not text:
        return default_baseline_y
    try:
        bbox = draw.textbbox(
            (0, 0),
            text,
            font=font,
            anchor="ls",
            embedded_color=True,
        )
    except (OSError, TypeError, ValueError):
        return default_baseline_y

    glyph_center_offset = (bbox[1] + bbox[3]) / 2
    line_center_y = line_top + (line_height / 2)
    return round(line_center_y - glyph_center_offset)


def _draw_footer(
    draw: ImageDraw.ImageDraw,
    tweet: dict[str, Any],
    xy: tuple[int, int],
    fonts: dict[str, Font],
) -> None:
    x = float(xy[0])
    y = xy[1]
    view_count = _extract_view_count(tweet)
    if view_count is None:
        draw.text((x, y), _format_created_at(tweet), font=fonts["footer"], fill=GRAY)
        return

    prefix = f"{_format_created_at(tweet)} · "
    views_text = _format_view_count(view_count)
    draw.text((x, y), prefix, font=fonts["footer"], fill=GRAY)
    x += draw.textlength(prefix, font=fonts["footer"])
    draw.text((x, y), views_text, font=fonts["footer_bold"], fill=BLACK)
    x += draw.textlength(views_text, font=fonts["footer_bold"])
    draw.text((x, y), " 浏览", font=fonts["footer"], fill=GRAY)


def _draw_footer_logo(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    source: Image.Image,
    size: tuple[int, int],
    xy: tuple[int, int],
    fonts: dict[str, Font],
    width: int,
) -> None:
    x, y = xy
    logo_width, logo_height = size
    text_bottom = _footer_text_bottom(draw, y, fonts["footer"])
    logo_x = x + width - logo_width
    logo_y = text_bottom - logo_height
    _draw_image(image, source, (logo_x, logo_y), size)


def _footer_text_bottom(draw: ImageDraw.ImageDraw, y: int, font: Font) -> int:
    sample = "上午9:02 · 2026年5月27日"
    try:
        return int(draw.textbbox((0, y), sample, font=font)[3])
    except AttributeError:
        ascent, descent = _font_metrics(font)
        return y + ascent + descent


def _draw_avatar(
    image: Image.Image,
    avatar: Image.Image,
    xy: tuple[int, int],
    size: int,
    *,
    radius: int | None = None,
) -> None:
    _draw_rounded_image(
        image,
        avatar,
        xy,
        (size, size),
        radius=radius if radius is not None else size // 2,
    )


def _draw_rounded_image(
    image: Image.Image,
    source: Image.Image,
    xy: tuple[int, int],
    size: tuple[int, int],
    *,
    radius: int,
) -> None:
    prepared = ImageOps.fit(
        source.convert("RGB"), size, method=Image.Resampling.LANCZOS
    )
    mask = Image.new("L", size, 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle((0, 0, size[0], size[1]), radius=radius, fill=255)
    image.paste(prepared, xy, mask)


def _draw_image(
    image: Image.Image,
    source: Image.Image,
    xy: tuple[int, int],
    size: tuple[int, int],
) -> None:
    prepared = source.convert("RGBA").resize(size, Image.Resampling.LANCZOS)
    alpha = prepared.getchannel("A")
    image.paste(prepared.convert("RGB"), xy, alpha)


def _scaled_height(source: Image.Image, target_width: int) -> int:
    source_width, source_height = source.size
    if source_width <= 0 or source_height <= 0:
        return max(1, target_width * 9 // 16)
    return max(1, round(target_width * source_height / source_width))


def _ellipsize(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: Font,
    max_width: int,
) -> str:
    if draw.textlength(text, font=font) <= max_width:
        return text
    suffix = "..."
    while text and draw.textlength(text + suffix, font=font) > max_width:
        text = text[:-1]
    return text + suffix if text else suffix


def _parse_rich_text(tweet: dict[str, Any]) -> list[TextSegment]:
    text = str(tweet.get("text") or "")
    display_map = _url_display_map(tweet)
    segments: list[TextSegment] = []
    position = 0

    matches = sorted(
        list(URL_RE.finditer(text)) + list(HASHTAG_RE.finditer(text)),
        key=lambda match: match.start(),
    )
    for match in matches:
        if match.start() < position:
            continue
        if match.start() > position:
            segments.append(TextSegment(text[position : match.start()]))

        raw_token = match.group(0)
        if raw_token.startswith("http://") or raw_token.startswith("https://"):
            token, trailing = _split_url_trailing_punctuation(raw_token)
            display = display_map.get(token, token)
            segments.append(TextSegment(display, "link", BLUE, True))
            if trailing:
                segments.append(TextSegment(trailing))
        else:
            segments.append(TextSegment(raw_token, "hashtag", BLUE, False))
        position = match.end()

    if position < len(text):
        segments.append(TextSegment(text[position:]))
    if not segments:
        return [TextSegment("(no text)")]
    return segments


def _tweet_for_body_text(
    tweet: dict[str, Any],
    options: dict[str, Any],
) -> dict[str, Any]:
    raw_text = (
        str(options.get("text_override") or "")
        if "text_override" in options
        else str(tweet.get("text") or "")
    )
    render_text = _normalize_text_for_rendering(raw_text)
    if "text_override" not in options and render_text == raw_text:
        return tweet
    body_tweet = dict(tweet)
    body_tweet["text"] = render_text
    return body_tweet


def _normalize_text_for_rendering(text: str) -> str:
    return text.translate(TEXT_GLYPH_COMPATIBILITY_MAP)


def _split_url_trailing_punctuation(raw_url: str) -> tuple[str, str]:
    token = raw_url
    trailing = ""
    while token and token[-1] in TRAILING_URL_PUNCTUATION:
        trailing = token[-1] + trailing
        token = token[:-1]
    return token, trailing


def _url_display_map(tweet: dict[str, Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    entities = tweet.get("entities") or {}
    for entry in entities.get("urls") or []:
        if not isinstance(entry, dict):
            continue
        display = entry.get("display_url") or entry.get("displayUrl")
        if not display:
            continue
        for key in ("url", "expanded_url", "expandedUrl"):
            raw_value = entry.get(key)
            if raw_value:
                mapping[str(raw_value)] = str(display)
    return mapping


def _wrap_segments(
    draw: ImageDraw.ImageDraw,
    segments: list[TextSegment],
    font: Font,
    max_width: int,
    *,
    emoji_font: Font | None = None,
) -> list[list[TextSegment]]:
    lines: list[list[TextSegment]] = []
    current: list[TextSegment] = []
    current_width = 0.0

    def flush() -> None:
        nonlocal current, current_width
        lines.append(current or [TextSegment("")])
        current = []
        current_width = 0.0

    for segment in segments:
        pieces = _split_wrappable_text(segment.text)
        for piece in pieces:
            if piece == "\n":
                flush()
                continue
            if not piece:
                continue
            if piece.isspace() and not current:
                continue

            piece_segment = TextSegment(
                piece, segment.kind, segment.fill, segment.underline
            )
            piece_width = _textlength(draw, piece, font, emoji_font)
            if current and current_width + piece_width > max_width:
                flush()
                if piece.isspace():
                    continue

            if piece_width <= max_width:
                current.append(piece_segment)
                current_width += piece_width
                continue

            for char_segment in _break_long_piece(
                draw,
                piece_segment,
                font,
                max_width,
                emoji_font,
            ):
                char_width = _textlength(draw, char_segment.text, font, emoji_font)
                if current and current_width + char_width > max_width:
                    flush()
                current.append(char_segment)
                current_width += char_width

    if current or not lines:
        flush()
    return lines


def _split_wrappable_text(text: str) -> list[str]:
    pieces: list[str] = []
    for index, line in enumerate(text.split("\n")):
        if index > 0:
            pieces.append("\n")
        pieces.extend(re.findall(r"\s+|[^\s]+", line))
    return pieces


def _break_long_piece(
    draw: ImageDraw.ImageDraw,
    segment: TextSegment,
    font: Font,
    max_width: int,
    emoji_font: Font | None = None,
) -> list[TextSegment]:
    pieces: list[TextSegment] = []
    current = ""
    for char in segment.text:
        candidate = current + char
        if current and _textlength(draw, candidate, font, emoji_font) > max_width:
            pieces.append(
                TextSegment(current, segment.kind, segment.fill, segment.underline)
            )
            current = char
        else:
            current = candidate
    if current:
        pieces.append(
            TextSegment(current, segment.kind, segment.fill, segment.underline)
        )
    return pieces


def _textlength(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: Font,
    emoji_font: Font | None = None,
) -> float:
    return sum(
        draw.textlength(run_text, font=run_font)
        for run_text, run_font, _embedded_color in _iter_font_runs(
            text,
            font,
            emoji_font,
        )
    )


def _iter_font_runs(
    text: str,
    font: Font,
    emoji_font: Font | None = None,
) -> list[tuple[str, Font, bool]]:
    if emoji_font is None or not text:
        return [(text, font, False)] if text else []

    runs: list[tuple[str, Font, bool]] = []
    current = ""
    current_is_emoji = False
    for char in text:
        is_emoji = _is_emoji_char(char)
        if current and is_emoji != current_is_emoji:
            runs.append(
                (current, emoji_font if current_is_emoji else font, current_is_emoji)
            )
            current = char
        else:
            current += char
        current_is_emoji = is_emoji

    if current:
        runs.append((current, emoji_font if current_is_emoji else font, current_is_emoji))
    return runs


def _is_emoji_char(char: str) -> bool:
    codepoint = ord(char)
    return (
        0x1F000 <= codepoint <= 0x1FAFF
        or 0x2600 <= codepoint <= 0x27BF
        or codepoint in {0x200D, 0xFE0E, 0xFE0F}
    )


def _format_footer(tweet: dict[str, Any]) -> str:
    parts = [_format_created_at(tweet)]
    view_count = _extract_view_count(tweet)
    if view_count is not None:
        parts.append(f"{_format_view_count(view_count)} 浏览")
    return " · ".join(parts)


def _format_card_source_line(tweet: dict[str, Any]) -> str | None:
    domain = _extract_card_source_domain(tweet)
    return f"From {domain}" if domain else None


def _extract_card_source_domain(tweet: dict[str, Any]) -> str | None:
    card = tweet.get("card") or {}
    if isinstance(card, dict):
        for key in (
            "domain",
            "site",
            "source",
            "publisher",
            "url",
            "card_url",
            "cardUrl",
            "vanity_url",
            "vanityUrl",
        ):
            domain = _domain_from_unknown(card.get(key))
            if domain:
                return domain

        binding_values = card.get("binding_values") or card.get("bindingValues") or {}
        for key, value in _iter_card_binding_items(binding_values):
            if not _looks_like_source_binding(key):
                continue
            domain = _domain_from_unknown(_binding_string_value(value))
            if domain:
                return domain

    entities = tweet.get("entities") or {}
    if isinstance(entities, dict):
        for entry in entities.get("urls") or []:
            if not isinstance(entry, dict):
                continue
            for key in (
                "display_url",
                "displayUrl",
                "expanded_url",
                "expandedUrl",
                "url",
            ):
                domain = _domain_from_unknown(entry.get(key))
                if domain:
                    return domain
    return None


def _format_created_at(tweet: dict[str, Any]) -> str:
    parsed = _parse_created_at(tweet.get("createdAt"))
    if parsed is None:
        return str(tweet.get("createdAt") or "unknown time")

    shanghai = parsed.astimezone(timezone(timedelta(hours=8)))
    hour = shanghai.hour % 12 or 12
    minute = f"{shanghai.minute:02d}"
    meridiem = "上午" if shanghai.hour < 12 else "下午"
    return f"{meridiem}{hour}:{minute} · {shanghai.year}年{shanghai.month}月{shanghai.day}日"


def _parse_created_at(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        raw_text = str(value).strip()
        parsed = None
        for parser in (
            lambda text: datetime.strptime(text, "%a %b %d %H:%M:%S %z %Y"),
            lambda text: datetime.fromisoformat(text.replace("Z", "+00:00")),
        ):
            try:
                parsed = parser(raw_text)
                break
            except ValueError:
                continue
        if parsed is None:
            return None
    if parsed is None:
        return None
    parsed_at = parsed
    if parsed_at.tzinfo is None:
        parsed_at = parsed_at.replace(tzinfo=timezone.utc)
    return parsed_at


def _extract_view_count(tweet: dict[str, Any]) -> int | None:
    candidates = (
        tweet.get("viewCount"),
        tweet.get("view_count"),
        tweet.get("views"),
        (tweet.get("public_metrics") or {}).get("impression_count")
        if isinstance(tweet.get("public_metrics"), dict)
        else None,
    )
    for candidate in candidates:
        if candidate is None or candidate == "":
            continue
        try:
            return int(candidate)
        except (TypeError, ValueError):
            continue
    return None


def _format_view_count(value: int) -> str:
    value = int(value)
    sign = "-" if value < 0 else ""
    value = abs(value)
    for threshold, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if value >= threshold:
            compact = (value // (threshold // 10)) / 10
            text = f"{compact:.1f}".rstrip("0").rstrip(".")
            return f"{sign}{text}{suffix}"
    return f"{sign}{value}"


def _load_avatar(
    tweet: dict[str, Any], options: dict[str, Any], size: int
) -> Image.Image:
    source = (
        options.get("avatar")
        or options.get("avatar_image")
        or options.get("avatar_source")
        or _author(tweet).get("profilePicture")
        or _author(tweet).get("profile_image_url_https")
        or _author(tweet).get("avatar")
    )
    image = _load_image_source(source, options)
    if image is not None:
        return image
    return _placeholder_avatar(tweet, size)


def _load_media(tweet: dict[str, Any], options: dict[str, Any]) -> Image.Image | None:
    option_media = (
        options.get("media")
        or options.get("media_image")
        or options.get("media_source")
        or _first(options.get("media_images"))
    )
    image = _load_image_source(option_media, options)
    if image is not None:
        return image

    for source in _tweet_media_sources(tweet):
        image = _load_image_source(source, options)
        if image is not None:
            return image
    return None


def _load_source_logo(
    tweet: dict[str, Any], options: dict[str, Any]
) -> Image.Image | None:
    source = (
        options.get("source_logo")
        or options.get("source_logo_image")
        or options.get("source_logo_source")
        or options.get("brand_logo")
        or options.get("brand_logo_path")
        or tweet.get("sourceLogo")
        or tweet.get("source_logo")
    )
    image = _load_image_source(source, options)
    if image is None:
        return None
    threshold = int(options.get("source_logo_alpha_threshold", 128))
    return _trim_image_border(image, alpha_threshold=threshold)


def _source_logo_position(options: dict[str, Any]) -> str:
    raw_position = str(
        options.get("source_logo_position")
        or options.get("logo_position")
        or DEFAULT_SOURCE_LOGO_POSITION
    ).strip().lower()
    if raw_position in {"body", "inline", "content"}:
        return "body"
    return "footer"


def _tweet_media_sources(tweet: dict[str, Any]) -> list[Any]:
    sources: list[Any] = []
    for container_name in ("extendedEntities", "extended_entities", "entities"):
        container = tweet.get(container_name) or {}
        for media in container.get("media") or []:
            if isinstance(media, dict):
                sources.extend(
                    media.get(key)
                    for key in (
                        "media_url_https",
                        "media_url",
                        "url",
                        "preview_image_url",
                    )
                    if media.get(key)
                )

    for media in tweet.get("media") or tweet.get("images") or []:
        if isinstance(media, dict):
            sources.extend(
                media.get(key)
                for key in ("media_url_https", "media_url", "url", "preview_image_url")
                if media.get(key)
            )
        else:
            sources.append(media)

    sources.extend(_card_image_sources(tweet))
    return _dedupe_values(source for source in sources if source)


def _card_image_sources(tweet: dict[str, Any]) -> list[Any]:
    card = tweet.get("card") or {}
    if not isinstance(card, dict):
        return []

    binding_values = card.get("binding_values") or card.get("bindingValues") or {}
    candidates: list[tuple[int, int, int, Any]] = []
    for index, (key, value) in enumerate(_iter_card_binding_items(binding_values)):
        for source, width, height in _extract_card_image_candidates(value, key):
            priority = _card_image_priority(key, source)
            candidates.append((priority, width * height, -index, source))

    candidates.sort(reverse=True)
    return _dedupe_values(source for _priority, _area, _index, source in candidates)


def _trim_image_border(image: Image.Image, *, alpha_threshold: int = 1) -> Image.Image:
    rgba = image.convert("RGBA")
    width, height = rgba.size
    alpha_threshold = min(255, max(1, int(alpha_threshold)))
    alpha_mask = rgba.getchannel("A").point(
        lambda value: 255 if value >= alpha_threshold else 0
    )
    alpha_bbox = alpha_mask.getbbox()
    if alpha_bbox and alpha_bbox != (0, 0, width, height):
        return rgba.crop(alpha_bbox)

    rgb = rgba.convert("RGB")
    background = Image.new("RGB", rgb.size, rgb.getpixel((0, 0)))
    diff = ImageChops.difference(rgb, background)
    bbox = diff.getbbox()
    if bbox:
        return rgba.crop(bbox)
    return rgba


def _iter_card_binding_items(binding_values: Any) -> list[tuple[str, Any]]:
    if isinstance(binding_values, dict):
        return [(str(key), value) for key, value in binding_values.items()]
    if isinstance(binding_values, list):
        items: list[tuple[str, Any]] = []
        for index, item in enumerate(binding_values):
            if isinstance(item, dict) and "value" in item:
                items.append(
                    (str(item.get("key") or item.get("name") or index), item["value"])
                )
            else:
                items.append((str(index), item))
        return items
    return []


def _extract_card_image_candidates(
    value: Any,
    key_hint: str,
    *,
    depth: int = 0,
) -> list[tuple[Any, int, int]]:
    if value is None or depth > 4:
        return []

    if isinstance(value, str):
        return _extract_card_string_image_candidate(value, key_hint)

    if isinstance(value, dict):
        candidates: list[tuple[Any, int, int]] = []
        width, height = _card_image_dimensions(value)
        candidates.extend(
            _extract_explicit_nested_card_image_candidates(value, key_hint, depth)
        )
        candidates.extend(
            _extract_direct_card_image_source_candidates(
                value, key_hint, width, height
            )
        )
        candidates.extend(
            _extract_hinted_nested_card_image_candidates(value, key_hint, depth)
        )
        return candidates

    return []


_EXPLICIT_CARD_IMAGE_KEYS = ("image_value", "imageValue", "photo_image", "photoImage")
_DIRECT_CARD_IMAGE_SOURCE_KEYS = (
    "url",
    "image_url",
    "imageUrl",
    "media_url_https",
    "media_url",
    "secure_url",
)
_CARD_IMAGE_KEYS_HANDLED_DIRECTLY = frozenset(
    (*_EXPLICIT_CARD_IMAGE_KEYS, *_DIRECT_CARD_IMAGE_SOURCE_KEYS)
)


def _extract_card_string_image_candidate(
    value: str,
    key_hint: str,
) -> list[tuple[Any, int, int]]:
    if _looks_like_image_binding(key_hint) or _looks_like_image_source(value):
        return [(value, 0, 0)]
    return []


def _card_image_dimensions(value: dict[str, Any]) -> tuple[int, int]:
    width = _safe_int(value.get("width") or value.get("w"))
    height = _safe_int(value.get("height") or value.get("h"))
    return width, height


def _extract_explicit_nested_card_image_candidates(
    value: dict[str, Any],
    key_hint: str,
    depth: int,
) -> list[tuple[Any, int, int]]:
    candidates: list[tuple[Any, int, int]] = []
    for nested_key in _EXPLICIT_CARD_IMAGE_KEYS:
        nested_value = value.get(nested_key)
        if nested_value is not None:
            candidates.extend(
                _extract_card_image_candidates(
                    nested_value,
                    f"{key_hint}_{nested_key}",
                    depth=depth + 1,
                )
            )
    return candidates


def _extract_direct_card_image_source_candidates(
    value: dict[str, Any],
    key_hint: str,
    width: int,
    height: int,
) -> list[tuple[Any, int, int]]:
    candidates: list[tuple[Any, int, int]] = []
    for source_key in _DIRECT_CARD_IMAGE_SOURCE_KEYS:
        source = value.get(source_key)
        if source and (
            _looks_like_image_binding(key_hint) or _looks_like_image_source(source)
        ):
            candidates.append((source, width, height))
    return candidates


def _extract_hinted_nested_card_image_candidates(
    value: dict[str, Any],
    key_hint: str,
    depth: int,
) -> list[tuple[Any, int, int]]:
    candidates: list[tuple[Any, int, int]] = []
    for nested_key, nested_value in value.items():
        if nested_key in _CARD_IMAGE_KEYS_HANDLED_DIRECTLY:
            continue
        if _looks_like_image_binding(str(nested_key)):
            candidates.extend(
                _extract_card_image_candidates(
                    nested_value,
                    f"{key_hint}_{nested_key}",
                    depth=depth + 1,
                )
            )
    return candidates


def _card_image_priority(key: str, source: Any) -> int:
    normalized = f"{key} {source}".lower()
    score = 0
    if "original" in normalized:
        score += 80
    if "full" in normalized:
        score += 70
    if "large" in normalized:
        score += 60
    if "medium" in normalized:
        score += 30
    if "small" in normalized or "thumb" in normalized:
        score += 10
    if "photo" in normalized or "image" in normalized:
        score += 20
    return score


def _looks_like_image_binding(key: str) -> bool:
    normalized = key.lower()
    return any(
        token in normalized for token in ("image", "photo", "thumbnail", "media")
    )


def _looks_like_image_source(value: Any) -> bool:
    text = str(value).lower()
    return text.startswith(("http://", "https://", "/")) and any(
        token in text
        for token in (
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
            ".gif",
            "format=jpg",
            "format=png",
        )
    )


def _looks_like_source_binding(key: str) -> bool:
    normalized = key.lower()
    return any(
        token in normalized
        for token in ("domain", "site", "source", "publisher", "url")
    )


def _binding_string_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in (
            "string_value",
            "stringValue",
            "scribe_key",
            "scribeKey",
            "vanity_url",
            "vanityUrl",
            "url",
        ):
            if value.get(key):
                return str(value[key])
        nested = value.get("value")
        if nested is not None and nested is not value:
            return _binding_string_value(nested)
    return None


def _domain_from_unknown(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if "://" not in text and not text.startswith("//"):
        text = text.split()[0].split("/")[0]
        return text.lower().removeprefix("www.") if "." in text else None

    parsed = urlparse(text if "://" in text else f"https:{text}")
    domain = (parsed.netloc or "").split("@")[-1].split(":")[0].lower()
    return domain.removeprefix("www.") or None


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _dedupe_values(values: Any) -> list[Any]:
    results: list[Any] = []
    seen: set[str] = set()
    for value in values:
        key = str(value)
        if key in seen:
            continue
        seen.add(key)
        results.append(value)
    return results


def _load_image_source(source: Any, options: dict[str, Any]) -> Image.Image | None:
    if source is None:
        return None
    if isinstance(source, Image.Image):
        return source.copy().convert("RGBA")
    if isinstance(source, (bytes, bytearray, memoryview)):
        try:
            return Image.open(io.BytesIO(bytes(source))).convert("RGBA")
        except OSError:
            return None
    if isinstance(source, Path):
        return _open_local_image(source)
    if isinstance(source, str):
        if source.startswith(("http://", "https://")):
            if options.get("fetch_remote_images", True) is False:
                return None
            if not _is_safe_remote_image_url(source, options):
                return None
            return _open_remote_image(source, float(options.get("remote_timeout", 3.0)))
        return _open_local_image(Path(source))
    return None


def _is_safe_remote_image_url(url: str, options: dict[str, Any]) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False

    host = (parsed.hostname or "").strip().lower()
    if not host:
        return False

    allowed_hosts = options.get("allowed_remote_hosts")
    if allowed_hosts:
        allowed = {str(item).lower().lstrip(".") for item in allowed_hosts}
        if not any(host == item or host.endswith(f".{item}") for item in allowed):
            return False

    if host == "localhost" or host.endswith(".localhost"):
        return False

    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            resolved = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except OSError:
            return False
        addresses = []
        for item in resolved:
            address = item[4][0]
            try:
                addresses.append(ipaddress.ip_address(address))
            except ValueError:
                return False

    return bool(addresses) and all(_is_public_address(address) for address in addresses)


def _is_public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _open_local_image(path: Path) -> Image.Image | None:
    try:
        if not path.exists():
            return None
        return Image.open(path).convert("RGBA")
    except OSError:
        return None


def _open_remote_image(url: str, timeout: float) -> Image.Image | None:
    try:
        request = Request(url, headers={"User-Agent": "xmonitor-pillow-renderer/1.0"})
        with urlopen(request, timeout=timeout) as response:
            data = response.read(5 * 1024 * 1024)
        return Image.open(io.BytesIO(data)).convert("RGBA")
    except (OSError, URLError, TimeoutError, ValueError):
        return None


def _placeholder_avatar(tweet: dict[str, Any], size: int) -> Image.Image:
    author = _author(tweet)
    name = str(author.get("name") or author.get("userName") or "?")
    initial = (name.strip()[:1] or "?").upper()
    image = Image.new("RGB", (size, size), (29, 155, 240))
    draw = ImageDraw.Draw(image)
    font = _load_font([], max(16, size // 2), require_cjk=False)
    bbox = draw.textbbox((0, 0), initial, font=font)
    draw.text(
        ((size - (bbox[2] - bbox[0])) / 2, (size - (bbox[3] - bbox[1])) / 2 - 2),
        initial,
        fill=WHITE,
        font=font,
    )
    return image


def _author(tweet: dict[str, Any]) -> dict[str, Any]:
    author = tweet.get("author") or tweet.get("user") or {}
    return author if isinstance(author, dict) else {}


def _first(value: Any) -> Any:
    if isinstance(value, (list, tuple)) and value:
        return value[0]
    return value


__all__ = ["render_image", "render_to_base64", "render_to_file"]
