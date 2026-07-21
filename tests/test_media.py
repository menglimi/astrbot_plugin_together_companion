# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "media.py"
SPEC = importlib.util.spec_from_file_location("together_companion_media", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MEDIA = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MEDIA
SPEC.loader.exec_module(MEDIA)


class BilibiliMediaResolverTests(unittest.TestCase):
    def test_supported_bilibili_hosts(self) -> None:
        self.assertTrue(MEDIA.is_bilibili_page_url("https://www.bilibili.com/video/BV1PgKr6uExx"))
        self.assertTrue(MEDIA.is_bilibili_page_url("https://b23.tv/example"))
        self.assertFalse(MEDIA.is_bilibili_page_url("https://example.com/video/BV1PgKr6uExx"))

    def test_bvid_and_page_are_extracted(self) -> None:
        resolver = MEDIA.BilibiliMediaResolver(Path("."))
        url = "https://www.bilibili.com/video/BV1PgKr6uExx/?p=2"
        self.assertEqual("BV1PgKr6uExx", resolver._extract_bvid(url))
        pages = {"pages": [{"page": 1, "cid": 11}, {"page": 2, "cid": 22}]}
        self.assertEqual(22, resolver._select_page(pages, url)["cid"])

    def test_cookie_is_loaded_without_control_characters(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config_dir = root / "config"
            config_dir.mkdir()
            (config_dir / "astrbot_plugin_bilibili_bot_config.json").write_text(
                json.dumps({"SESSDATA": "abc\r\n;def", "BILI_JCT": "token", "BUVID4": "device-4"}),
                encoding="utf-8",
            )
            cookie = MEDIA.BilibiliMediaResolver(root)._bilibili_cookie()
            self.assertEqual("SESSDATA=abcdef; bili_jct=token; buvid4=device-4", cookie)

    def test_runtime_headers_override_disk_headers(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config_dir = root / "config"
            config_dir.mkdir()
            (config_dir / "astrbot_plugin_bilibili_bot_config.json").write_text(
                json.dumps({"SESSDATA": "disk", "BILI_JCT": "disk-token"}),
                encoding="utf-8",
            )
            headers = MEDIA.BilibiliMediaResolver(root)._api_headers(
                {
                    "Cookie": "SESSDATA=runtime; buvid4=runtime-device",
                    "User-Agent": "Chrome/120",
                }
            )
            self.assertEqual("SESSDATA=runtime; buvid4=runtime-device", headers["Cookie"])
            self.assertEqual("Chrome/120", headers["User-Agent"])


class BilibiliSubtitleTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_tags_are_deduplicated(self) -> None:
        class Resolver(MEDIA.BilibiliMediaResolver):
            async def _get_json(self, _session, _url, *, params):
                return {
                    "code": 0,
                    "data": [
                        {"tag_name": "动画"},
                        {"tag_name": "动画"},
                        {"tag_name": " 异世界 "},
                    ],
                }

        tags = await Resolver(Path("."))._fetch_tags(object(), "BV1PgKr6uExx")
        self.assertEqual(["动画", "异世界"], tags)

    async def test_subtitle_track_is_normalized_to_timeline_cues(self) -> None:
        class Resolver(MEDIA.BilibiliMediaResolver):
            async def _get_json(self, _session, url, *, params):
                if url.endswith("/x/player/v2"):
                    return {
                        "code": 0,
                        "data": {
                            "subtitle": {
                                "subtitles": [
                                    {
                                        "lan": "zh-CN",
                                        "lan_doc": "中文",
                                        "subtitle_url": "//example.com/subtitle.json",
                                    }
                                ]
                            }
                        },
                    }
                return {
                    "body": [
                        {"from": 1.2, "to": 2.8, "content": "  第一  句  "},
                        {"from": 3, "to": 4, "content": "第二句"},
                    ]
                }

        cues, language = await Resolver(Path("."))._fetch_subtitles(object(), "BV1PgKr6uExx", 123)
        self.assertEqual("中文", language)
        self.assertEqual(
            [
                {"start": 1.2, "end": 2.8, "text": "第一 句"},
                {"start": 3.0, "end": 4.0, "text": "第二句"},
            ],
            cues,
        )


class BilibiliQualityTests(unittest.IsolatedAsyncioTestCase):
    async def test_dash_requests_try_look_and_selects_avc_aac_tracks(self) -> None:
        calls = []

        class Resolver(MEDIA.BilibiliMediaResolver):
            async def _get_json(self, _session, _url, *, params):
                calls.append(dict(params))
                return {
                    "code": 0,
                    "data": {
                        "quality": 64,
                        "dash": {
                            "video": [
                                {
                                    "id": 120,
                                    "baseUrl": "https://example.com/4k.mp4",
                                    "mimeType": "video/mp4",
                                    "codecs": "avc1.640033",
                                    "bandwidth": 1800,
                                },
                                {
                                    "id": 80,
                                    "baseUrl": "https://example.com/hevc.mp4",
                                    "mimeType": "video/mp4",
                                    "codecs": "hvc1.1.6.L120.90",
                                    "bandwidth": 900,
                                },
                                {
                                    "id": 80,
                                    "baseUrl": "https://example.com/avc.mp4",
                                    "mimeType": "video/mp4",
                                    "codecs": "avc1.640028",
                                    "bandwidth": 800,
                                },
                                {
                                    "id": 64,
                                    "baseUrl": "https://example.com/720.mp4",
                                    "mimeType": "video/mp4",
                                    "codecs": "avc1.64001f",
                                    "bandwidth": 700,
                                },
                            ],
                            "audio": [
                                {
                                    "id": 30280,
                                    "baseUrl": "https://example.com/audio.m4a",
                                    "mimeType": "audio/mp4",
                                    "codecs": "mp4a.40.2",
                                    "bandwidth": 500,
                                }
                            ],
                        },
                    },
                }

        play_data, video, audio = await Resolver(Path("."))._fetch_dash_stream(
            object(),
            "BV1xx411c7mD",
            123,
        )

        self.assertEqual("127", calls[0]["qn"])
        self.assertEqual("4048", calls[0]["fnval"])
        self.assertEqual("1", calls[0]["try_look"])
        self.assertEqual("https://example.com/4k.mp4", video["url"])
        self.assertEqual("https://example.com/audio.m4a", audio["url"])
        self.assertEqual(120, play_data["quality"])

    async def test_progressive_stream_requests_highest_quality_first(self) -> None:
        calls = []

        class Resolver(MEDIA.BilibiliMediaResolver):
            async def _get_json(self, _session, _url, *, params):
                calls.append(dict(params))
                return {
                    "code": 0,
                    "data": {
                        "quality": 120,
                        "durl": [{"url": "https://example.com/4k.mp4"}],
                    },
                }

        play_data, stream = await Resolver(Path("."))._fetch_progressive_stream(
            object(),
            "BV1xx411c7mD",
            123,
        )

        self.assertEqual("127", calls[0]["qn"])
        self.assertEqual("0", calls[0]["fnval"])
        self.assertEqual("0", calls[0]["fnver"])
        self.assertEqual("https://example.com/4k.mp4", stream["url"])
        self.assertEqual(120, play_data["quality"])

    async def test_progressive_stream_falls_back_when_highest_has_no_merged_stream(self) -> None:
        calls = []

        class Resolver(MEDIA.BilibiliMediaResolver):
            async def _get_json(self, _session, _url, *, params):
                calls.append(dict(params))
                if params["qn"] == "127":
                    return {"code": 0, "data": {"quality": 80, "durl": []}}
                return {
                    "code": 0,
                    "data": {
                        "quality": 80,
                        "durl": [{"url": "https://example.com/1080p.mp4"}],
                    },
                }

        play_data, stream = await Resolver(Path("."))._fetch_progressive_stream(
            object(),
            "BV1xx411c7mD",
            123,
        )

        self.assertEqual(["127", "80"], [item["qn"] for item in calls])
        self.assertEqual("https://example.com/1080p.mp4", stream["url"])
        self.assertEqual(80, play_data["quality"])


if __name__ == "__main__":
    unittest.main()
