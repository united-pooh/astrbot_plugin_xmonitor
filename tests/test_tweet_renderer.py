from __future__ import annotations

import base64
import io
import tempfile
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import patch

from PIL import Image, ImageDraw

import tweet_renderer
from tweet_renderer import render_image, render_to_base64, render_to_file


def _sample_tweet(**overrides):
    tweet = {
        "id": "123",
        "text": "Hello https://t.co/abc #BlueArchive",
        "createdAt": "Wed May 01 01:02:00 +0000 2024",
        "viewCount": 252967,
        "author": {
            "name": "Blue Archive",
            "userName": "Blue_ArchiveJP",
        },
        "entities": {
            "urls": [
                {
                    "url": "https://t.co/abc",
                    "expanded_url": "https://example.com/news",
                    "display_url": "example.com/news",
                }
            ]
        },
    }
    tweet.update(overrides)
    return tweet


def _image(color, size=(64, 64)):
    return Image.new("RGB", size, color)


def _blueish(pixel) -> bool:
    red, green, blue = pixel[:3]
    return red < 90 and green > 110 and blue > 170


def _greenish(pixel) -> bool:
    red, green, blue = pixel[:3]
    return red < 80 and green > 120 and blue < 140


def _orangeish(pixel) -> bool:
    red, green, blue = pixel[:3]
    return red > 180 and 80 < green < 180 and blue < 80


def _goldish(pixel) -> bool:
    red, green, blue = pixel[:3]
    return red > 180 and 130 < green < 220 and blue < 80


def _blackish(pixel) -> bool:
    red, green, blue = pixel[:3]
    return red < 70 and green < 80 and blue < 90


def _pixel_bbox(image: Image.Image, predicate) -> tuple[int, int, int, int] | None:
    rgb_image = image.convert("RGB")
    pixels = rgb_image.load()
    assert pixels is not None
    min_x = rgb_image.width
    min_y = rgb_image.height
    max_x = -1
    max_y = -1
    for y in range(rgb_image.height):
        for x in range(rgb_image.width):
            if predicate(pixels[x, y]):
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x)
                max_y = max(max_y, y)
    if max_x < 0:
        return None
    return (min_x, min_y, max_x, max_y)


def _has_pixel_in_region(
    image: Image.Image, predicate, box: tuple[int, int, int, int]
) -> bool:
    rgb_image = image.convert("RGB")
    pixels = rgb_image.load()
    assert pixels is not None
    left, top, right, bottom = box
    for y in range(max(0, top), min(rgb_image.height, bottom)):
        for x in range(max(0, left), min(rgb_image.width, right)):
            if predicate(pixels[x, y]):
                return True
    return False


def _glyph_signature(
    font, character: str
) -> tuple[tuple[int, int, int, int] | None, bytes]:
    image = Image.new("L", (80, 80), 0)
    draw = ImageDraw.Draw(image)
    draw.text((8, 8), character, font=font, fill=255)
    bbox = image.getbbox()
    if bbox is None:
        return None, b""
    return bbox, image.crop(bbox).tobytes()


class TweetRendererTest(unittest.TestCase):
    def test_default_layout_parameters_match_x_detail_dom(self) -> None:
        self.assertEqual(tweet_renderer.DEFAULT_WIDTH, 1200)
        self.assertEqual(tweet_renderer.DEFAULT_MARGIN, 34)
        self.assertEqual(tweet_renderer.DEFAULT_AVATAR_SIZE, 80)
        self.assertEqual(tweet_renderer.DEFAULT_AVATAR_RADIUS, 16)
        self.assertEqual(tweet_renderer.DEFAULT_CONTENT_GAP, 20)
        self.assertEqual(tweet_renderer.DEFAULT_MEDIA_RADIUS, 32)
        self.assertEqual(tweet_renderer.DEFAULT_SOURCE_LOGO_POSITION, "footer")
        self.assertEqual(tweet_renderer.DEFAULT_BODY_FONT_SIZE, 34)
        self.assertEqual(tweet_renderer.DEFAULT_HEADER_FONT_SIZE, 30)
        self.assertEqual(tweet_renderer.DEFAULT_META_FONT_SIZE, 30)
        self.assertEqual(tweet_renderer.DEFAULT_FOOTER_FONT_SIZE, 30)

        fonts = tweet_renderer._load_fonts({})

        self.assertEqual(fonts["body"].size, 34)
        self.assertEqual(fonts["bold"].size, 30)
        self.assertEqual(fonts["meta"].size, 30)
        self.assertEqual(fonts["footer"].size, 30)
        self.assertIn(fonts["bold"].getname()[1], {"W6", "W7", "Bold"})
        self.assertIn(fonts["footer_bold"].getname()[1], {"W6", "W7", "Bold"})

    def test_default_render_width_is_1200_class(self) -> None:
        image = render_image(_sample_tweet())

        self.assertGreaterEqual(image.width, 1100)
        self.assertLessEqual(abs(image.width - 1200), 80)

    def test_render_to_base64_and_file_are_valid_png(self) -> None:
        payload = render_to_base64(_sample_tweet())
        decoded = base64.b64decode(payload)

        self.assertTrue(decoded.startswith(b"\x89PNG\r\n\x1a\n"))
        with Image.open(io.BytesIO(decoded)) as image:
            self.assertEqual(image.format, "PNG")
            self.assertGreater(image.size[0], 300)
            self.assertGreater(image.size[1], 120)

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "tweet.png"
            returned_path = render_to_file(_sample_tweet(), output_path)
            self.assertEqual(returned_path, output_path)
            with Image.open(output_path) as image:
                self.assertEqual(image.format, "PNG")

    def test_long_text_increases_height_without_clipping(self) -> None:
        short_image = render_image(_sample_tweet(text="Short update"))
        long_text = " ".join(["This is a long tweet body with wrapping"] * 30)
        long_image = render_image(_sample_tweet(text=long_text))

        self.assertGreater(long_image.height, short_image.height + 120)

    def test_body_text_starts_at_page_margin_not_avatar_column(self) -> None:
        image = render_image(
            _sample_tweet(text="#BlueArchive starts from the page margin")
        )

        self.assertTrue(
            _has_pixel_in_region(
                image,
                _blueish,
                (30, 175, 105, image.height - 40),
            )
        )

    def test_parses_links_hashtags_and_display_url(self) -> None:
        segments = tweet_renderer._parse_rich_text(_sample_tweet())

        link_segment = next(segment for segment in segments if segment.kind == "link")
        hashtag_segment = next(
            segment for segment in segments if segment.kind == "hashtag"
        )

        self.assertEqual(link_segment.text, "example.com/news")
        self.assertEqual(link_segment.fill, tweet_renderer.BLUE)
        self.assertTrue(link_segment.underline)
        self.assertEqual(hashtag_segment.text, "#BlueArchive")
        self.assertEqual(hashtag_segment.fill, tweet_renderer.BLUE)

    def test_display_url_link_renders_blue_near_margin(self) -> None:
        image = render_image(_sample_tweet(text="https://t.co/abc opens with a link"))

        self.assertTrue(
            _has_pixel_in_region(
                image,
                _blueish,
                (30, 175, 260, image.height - 40),
            )
        )

    def test_inline_link_and_hashtag_are_vertically_centered_with_text(self) -> None:
        image = render_image(
            _sample_tweet(text="同行正文 https://t.co/abc #碧蓝档案"),
            options={"show_translation": True, "translation_text": "显示翻译"},
        )
        body_crop = image.crop((30, 130, 900, 200))

        black_bbox = _pixel_bbox(body_crop, _blackish)
        blue_bbox = _pixel_bbox(body_crop, _blueish)

        self.assertIsNotNone(black_bbox)
        self.assertIsNotNone(blue_bbox)
        assert black_bbox is not None
        assert blue_bbox is not None
        self.assertLess(abs(black_bbox[1] - blue_bbox[1]), 4)
        self.assertLess(blue_bbox[3], body_crop.height - 10)

    def test_translation_row_is_never_rendered_even_when_requested(self) -> None:
        tweet = _sample_tweet(text="正文内容 #碧蓝档案")

        default_image = render_image(tweet)
        requested_image = render_image(
            tweet,
            options={"show_translation": True, "translation_text": "显示翻译"},
        )

        self.assertEqual(requested_image.size, default_image.size)
        self.assertEqual(requested_image.tobytes(), default_image.tobytes())

    def test_chinese_translation_renders_without_tofu_and_keeps_layout(self) -> None:
        translated_text = (
            "【定期维护通知】\n"
            "我们将在以下时间进行维护。\n"
            "老师，感谢您的配合！\n\n"
            "▼实施时间\n"
            "5/27（周三）11:00 ～ 19:00左右\n\n"
            "▼详情\n"
            "https://t.co/abc\n\n"
            "※结束时间可能会前后变动。\n"
            "※请提前发行绑定代码，并完成各类账号绑定设置。\n"
            "#碧蓝档案"
        )
        tweet = _sample_tweet(
            text=translated_text,
            author={"name": "蓝色档案官方", "userName": "Blue_ArchiveJP"},
            entities={
                "urls": [
                    {
                        "url": "https://t.co/abc",
                        "display_url": "bluearchive.jp/news/newsJump/...",
                    }
                ]
            },
        )

        fonts = tweet_renderer._load_fonts({})
        signatures = {
            _glyph_signature(fonts["body"], character)[1]
            for character in ("维", "护", "翻", "译")
        }
        self.assertEqual(len(signatures), 4)

        segments = tweet_renderer._parse_rich_text(tweet)
        self.assertTrue(any(segment.text == "#碧蓝档案" for segment in segments))
        self.assertTrue(
            any(
                segment.text == "bluearchive.jp/news/newsJump/..."
                and segment.underline
                for segment in segments
            )
        )

        image = render_image(
            tweet,
            options={"fetch_remote_images": False},
        )

        self.assertGreater(image.height, 700)
        self.assertFalse(
            _has_pixel_in_region(
                image,
                _blueish,
                (30, 120, 220, 180),
            )
        )
        self.assertTrue(
            _has_pixel_in_region(
                image,
                _blueish,
                (30, 300, 650, image.height - 80),
            )
        )

    def test_footer_uses_shanghai_time_and_compact_views(self) -> None:
        footer = tweet_renderer._format_footer(_sample_tweet())

        self.assertIn("上午9:02", footer)
        self.assertIn("2024年5月1日", footer)
        self.assertIn("252.9K 浏览", footer)

    def test_footer_draws_only_view_number_in_bold(self) -> None:
        class Recorder:
            def __init__(self):
                self.calls = []

            def text(self, xy, text, font, fill):
                self.calls.append((xy, text, font, fill))

            def textlength(self, text, font):
                return len(text) * 10

        recorder = Recorder()
        regular_font = object()
        bold_font = object()

        tweet_renderer._draw_footer(
            cast(ImageDraw.ImageDraw, recorder),
            _sample_tweet(),
            (34, 400),
            {"footer": regular_font, "footer_bold": bold_font},
        )

        self.assertEqual(len(recorder.calls), 3)
        self.assertIs(recorder.calls[0][2], regular_font)
        self.assertEqual(recorder.calls[0][3], tweet_renderer.GRAY)
        self.assertEqual(recorder.calls[1][1], "252.9K")
        self.assertIs(recorder.calls[1][2], bold_font)
        self.assertEqual(recorder.calls[1][3], tweet_renderer.BLACK)
        self.assertEqual(recorder.calls[2][1], " 浏览")
        self.assertIs(recorder.calls[2][2], regular_font)
        self.assertEqual(recorder.calls[2][3], tweet_renderer.GRAY)

    def test_header_badges_render_and_actions_are_hidden_by_default(self) -> None:
        tweet = _sample_tweet(
            text="Badge check",
            author={
                "name": "BA",
                "userName": "Blue_ArchiveJP",
                "isBlueVerified": True,
                "verifiedType": "Business",
            },
        )

        image = render_image(tweet)
        enabled_image = render_image(tweet, options={"show_header_actions": True})

        self.assertTrue(
            _has_pixel_in_region(
                image,
                _goldish,
                (165, 34, 220, 78),
            )
        )
        self.assertTrue(
            _has_pixel_in_region(
                image,
                _blackish,
                (200, 34, 255, 78),
            )
        )
        self.assertFalse(
            _has_pixel_in_region(
                image,
                _blackish,
                (1060, 30, 1125, 85),
            )
        )
        self.assertFalse(
            _has_pixel_in_region(
                image,
                lambda pixel: all(60 <= channel <= 130 for channel in pixel[:3]),
                (1120, 30, 1170, 85),
            )
        )
        self.assertTrue(
            _has_pixel_in_region(
                enabled_image,
                _blackish,
                (1060, 30, 1125, 85),
            )
        )
        self.assertTrue(
            _has_pixel_in_region(
                enabled_image,
                lambda pixel: all(60 <= channel <= 130 for channel in pixel[:3]),
                (1120, 30, 1170, 85),
            )
        )

    def test_avatar_and_media_options_are_rendered(self) -> None:
        media = _image((20, 180, 80), (320, 180))
        image = render_image(
            _sample_tweet(text="With media"),
            options={
                "avatar": _image((220, 30, 30)),
                "media": media,
            },
        )
        without_media = render_image(_sample_tweet(text="With media"))

        self.assertGreater(image.height, without_media.height + 120)
        green_pixels = 0
        rgb_image = image.convert("RGB")
        pixels = rgb_image.load()
        assert pixels is not None
        for y in range(rgb_image.height):
            for x in range(rgb_image.width):
                pixel = pixels[x, y]
                assert isinstance(pixel, tuple)
                red, green, blue = pixel[:3]
                if green > 120 and red < 80 and blue < 120:
                    green_pixels += 1
        self.assertGreater(green_pixels, 5000)

    def test_text_override_reuses_media_and_renders_translation_body(self) -> None:
        media = _image((20, 180, 80), (320, 180))
        tweet = _sample_tweet(text="plain original text", entities={"urls": []})

        original_image = render_image(tweet, options={"media": media})
        translated_image = render_image(
            tweet,
            options={
                "media": media,
                "text_override": "翻译正文\n#测试",
            },
        )

        self.assertFalse(
            _has_pixel_in_region(
                original_image,
                _blueish,
                (30, 130, 240, 260),
            )
        )
        self.assertTrue(
            _has_pixel_in_region(
                translated_image,
                _blueish,
                (30, 130, 240, 260),
            )
        )
        self.assertIsNotNone(_pixel_bbox(translated_image, _greenish))

    def test_emoji_renders_as_nonblank_color_glyph_in_png(self) -> None:
        image = render_image(
            _sample_tweet(text="😀", entities={"urls": []}),
            options={"fetch_remote_images": False},
        )

        self.assertTrue(
            _has_pixel_in_region(
                image,
                lambda pixel: pixel[0] > 200 and pixel[1] > 120 and pixel[2] < 120,
                (30, 130, 150, 230),
            )
        )

    def test_inline_emoji_is_vertically_centered_in_text_line(self) -> None:
        fonts = tweet_renderer._load_fonts({})
        emoji_font = fonts.get("emoji")
        if emoji_font is None:
            self.skipTest("emoji font is not available")
        body_font = fonts["body"]
        line_top = 20
        line_height = tweet_renderer._line_height(
            body_font,
            tweet_renderer.DEFAULT_BODY_LINE_PADDING,
        )
        image = Image.new("RGBA", (240, 120), (255, 255, 255, 255))
        draw = ImageDraw.Draw(image)

        tweet_renderer._draw_rich_lines(
            draw,
            [[tweet_renderer.TextSegment("A😀B")]],
            (20, line_top),
            body_font,
            line_height,
            emoji_font=emoji_font,
        )

        emoji_bbox = _pixel_bbox(
            image,
            lambda pixel: pixel[0] > 200 and pixel[1] > 120 and pixel[2] < 120,
        )
        self.assertIsNotNone(emoji_bbox)
        assert emoji_bbox is not None
        emoji_center_y = (emoji_bbox[1] + emoji_bbox[3]) / 2
        line_center_y = line_top + (line_height / 2)
        self.assertLessEqual(abs(emoji_center_y - line_center_y), 3)

    def test_media_override_spans_almost_full_card_width_from_margin(self) -> None:
        image = render_image(
            _sample_tweet(text="With wide media"),
            options={
                "avatar": _image((220, 30, 30)),
                "media": _image((20, 180, 80), (320, 180)),
            },
        )

        bbox = _pixel_bbox(image, _greenish)
        self.assertIsNotNone(bbox)
        assert bbox is not None
        min_x, _min_y, max_x, _max_y = bbox
        self.assertLessEqual(min_x, 36)
        self.assertGreaterEqual(max_x, image.width - 38)
        self.assertGreater(max_x - min_x, image.width - 90)

    def test_list_form_card_binding_uses_preferred_large_image(self) -> None:
        loaded_sources = []
        tweet = _sample_tweet(
            text="Card image",
            card={
                "binding_values": [
                    {
                        "key": "thumbnail_image",
                        "value": {
                            "image_value": {
                                "url": "https://cdn.example/small.jpg",
                                "width": 320,
                                "height": 180,
                            }
                        },
                    },
                    {
                        "key": "thumbnail_image_original",
                        "value": {
                            "image_value": {
                                "url": "https://cdn.example/original.jpg",
                                "width": 1600,
                                "height": 900,
                            }
                        },
                    },
                ]
            },
        )

        def fake_loader(source, _options):
            loaded_sources.append(source)
            if source == "https://cdn.example/original.jpg":
                return _image((230, 120, 20), (1600, 900))
            return None

        with patch.object(
            tweet_renderer, "_load_image_source", side_effect=fake_loader
        ):
            image = render_image(tweet)

        self.assertEqual(
            next(source for source in loaded_sources if source),
            "https://cdn.example/original.jpg",
        )
        self.assertIsNotNone(_pixel_bbox(image, _orangeish))

    def test_media_replaces_card_source_line_without_overlap(self) -> None:
        tweet = _sample_tweet(
            text="正文结束后显示标签\n#碧蓝档案",
            card={"domain": "https://www.bluearchive.jp/news"},
        )
        media = _image((20, 180, 80), (320, 180))
        logo = Image.new("RGBA", (320, 120), (0, 0, 0, 0))
        logo_draw = ImageDraw.Draw(logo)
        logo_draw.rectangle((80, 35, 280, 85), fill=(230, 120, 20, 255))

        with patch.object(
            tweet_renderer,
            "_format_card_source_line",
            side_effect=AssertionError("source line should not be rendered"),
        ):
            image = render_image(
                tweet,
                options={
                    "media": media,
                    "source_logo": logo,
                    "source_logo_position": "body",
                },
            )

        blue_bbox = _pixel_bbox(image, _blueish)
        orange_bbox = _pixel_bbox(image, _orangeish)
        green_bbox = _pixel_bbox(image, _greenish)
        self.assertIsNotNone(blue_bbox)
        self.assertIsNotNone(orange_bbox)
        self.assertIsNotNone(green_bbox)
        assert blue_bbox is not None
        assert orange_bbox is not None
        assert green_bbox is not None
        self.assertGreater(orange_bbox[1], blue_bbox[3] + 4)
        self.assertLessEqual(orange_bbox[3] - orange_bbox[1], 60)
        self.assertGreater(green_bbox[1], orange_bbox[3] + 18)
        self.assertGreater(green_bbox[3], green_bbox[1] + 120)
        self.assertGreater(image.height, green_bbox[3] + 40)

        footer_bbox = _pixel_bbox(
            image.crop((0, green_bbox[3] + 1, image.width, image.height)),
            _blackish,
        )
        self.assertIsNotNone(footer_bbox)

    def test_source_logo_defaults_to_footer_row_right_aligned(self) -> None:
        tweet = _sample_tweet(
            text="正文结束后显示标签\n#碧蓝档案",
            card={"domain": "https://www.bluearchive.jp/news"},
        )
        media = _image((20, 180, 80), (320, 180))
        logo = Image.new("RGBA", (320, 120), (0, 0, 0, 0))
        logo_draw = ImageDraw.Draw(logo)
        logo_draw.rectangle((80, 35, 280, 85), fill=(230, 120, 20, 255))

        with patch.object(
            tweet_renderer,
            "_format_card_source_line",
            side_effect=AssertionError("source line should not be rendered"),
        ):
            image = render_image(
                tweet,
                options={
                    "media": media,
                    "source_logo": logo,
                },
            )

        green_bbox = _pixel_bbox(image, _greenish)
        orange_bbox = _pixel_bbox(image, _orangeish)
        self.assertIsNotNone(green_bbox)
        self.assertIsNotNone(orange_bbox)
        assert green_bbox is not None
        assert orange_bbox is not None
        self.assertGreater(orange_bbox[1], green_bbox[3])
        self.assertGreaterEqual(orange_bbox[0], image.width - 240)
        self.assertLessEqual(orange_bbox[2], image.width - 32)

        footer_crop = image.crop((0, green_bbox[3] + 1, image.width, image.height))
        footer_black_bbox = _pixel_bbox(footer_crop, _blackish)
        footer_logo_bbox = _pixel_bbox(footer_crop, _orangeish)
        self.assertIsNotNone(footer_black_bbox)
        self.assertIsNotNone(footer_logo_bbox)
        assert footer_black_bbox is not None
        assert footer_logo_bbox is not None
        self.assertLessEqual(abs(footer_black_bbox[3] - footer_logo_bbox[3]), 3)

    def test_missing_or_disabled_remote_images_do_not_break_rendering(self) -> None:
        image = render_image(
            _sample_tweet(),
            options={
                "avatar": "https://example.invalid/avatar.png",
                "media": "https://example.invalid/media.png",
                "fetch_remote_images": False,
            },
        )

        self.assertIsInstance(image, Image.Image)

    def test_remote_image_url_rejects_private_or_disallowed_hosts(self) -> None:
        self.assertFalse(
            tweet_renderer._is_safe_remote_image_url(
                "https://127.0.0.1/avatar.png", {}
            )
        )
        self.assertFalse(
            tweet_renderer._is_safe_remote_image_url(
                "https://localhost/avatar.png", {}
            )
        )
        self.assertFalse(
            tweet_renderer._is_safe_remote_image_url(
                "https://pbs.twimg.com/avatar.png",
                {"allowed_remote_hosts": ["example.com"]},
            )
        )

    def test_extracts_media_from_twitterapi_card_binding_list(self) -> None:
        sources = tweet_renderer._tweet_media_sources(
            _sample_tweet(
                card={
                    "binding_values": [
                        {
                            "key": "summary_photo_image_large",
                            "value": {
                                "image_value": {
                                    "url": "https://pbs.twimg.com/card_img/sample.jpg",
                                    "width": 800,
                                    "height": 419,
                                }
                            },
                        }
                    ]
                }
            )
        )

        self.assertIn("https://pbs.twimg.com/card_img/sample.jpg", sources)

    def test_card_image_candidate_rejects_neutral_non_image_string(self) -> None:
        self.assertEqual(
            tweet_renderer._extract_card_image_candidates("plain title", "title"),
            [],
        )

    def test_card_image_candidate_accepts_image_url_under_neutral_key(self) -> None:
        self.assertEqual(
            tweet_renderer._extract_card_image_candidates(
                "https://cdn.example/card.jpg",
                "title",
            ),
            [("https://cdn.example/card.jpg", 0, 0)],
        )

    def test_card_image_candidate_preserves_explicit_nested_dimensions(self) -> None:
        self.assertEqual(
            tweet_renderer._extract_card_image_candidates(
                {
                    "image_value": {
                        "url": "https://cdn.example/image.jpg",
                        "width": 800,
                        "height": 419,
                    },
                    "photoImage": {
                        "url": "https://cdn.example/photo.png",
                        "w": 320,
                        "h": 180,
                    },
                },
                "summary",
            ),
            [
                ("https://cdn.example/image.jpg", 800, 419),
                ("https://cdn.example/photo.png", 320, 180),
            ],
        )

    def test_card_image_candidate_recurses_into_hinted_nested_key(self) -> None:
        self.assertEqual(
            tweet_renderer._extract_card_image_candidates(
                {
                    "thumbnail": {
                        "url": "https://cdn.example/image-service",
                        "width": 640,
                        "height": 360,
                    }
                },
                "summary",
            ),
            [("https://cdn.example/image-service", 640, 360)],
        )


if __name__ == "__main__":
    unittest.main()
