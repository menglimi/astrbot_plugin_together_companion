# -*- coding: utf-8 -*-
from __future__ import annotations

import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from astrbot_stubs import install_astrbot_stubs


install_astrbot_stubs()

from astrbot_plugin_together_companion.main import (
    TogetherCompanionPlugin,
    _is_local_dashboard_request,
    _is_loopback_address,
    request,
)
from astrbot_plugin_together_companion.models import RoomTicket


ROOT = Path(__file__).resolve().parents[1]


class LauncherTests(unittest.TestCase):
    def test_launcher_asks_local_backend_to_open_system_browser(self) -> None:
        source = (ROOT / "pages" / "一起房间" / "launcher.js").read_text(encoding="utf-8")

        self.assertIn("open_browser: true", source)
        self.assertIn("data.browser_opened", source)
        self.assertIn("已在系统默认浏览器打开房间", source)
        self.assertNotIn("window.open", source)
        self.assertNotIn("await copyUrl(data.url)", source)

    def test_launcher_contains_runtime_config_controls(self) -> None:
        page = (ROOT / "pages" / "一起房间" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "pages" / "一起房间" / "launcher.js").read_text(encoding="utf-8")

        self.assertIn('name="conversation.chat_provider_id"', page)
        self.assertIn('name="watch.comment_interval_seconds"', page)
        self.assertIn('role="switch"', page)
        self.assertIn('requestEndpoint("POST", "config/save"', script)
        self.assertNotIn('name="server.host"', page)
        self.assertNotIn('name="server.port"', page)

    def test_llm_tools_are_explicit_and_keep_video_selection_with_user(self) -> None:
        source = (ROOT / "main.py").read_text(encoding="utf-8")

        self.assertIn('@filter.llm_tool(name="open_together_watch_room")', source)
        self.assertIn('@filter.llm_tool(name="open_together_call_room")', source)
        self.assertIn("不会自动选择或播放视频", source)
        self.assertIn("明确表示要和 Bot 一起看视频", source)

    def test_loopback_detection_accepts_local_clients_only(self) -> None:
        self.assertTrue(_is_loopback_address("127.0.0.1"))
        self.assertTrue(_is_loopback_address("::1"))
        self.assertTrue(_is_loopback_address("::ffff:127.0.0.1"))
        self.assertTrue(_is_loopback_address("localhost"))
        self.assertFalse(_is_loopback_address("192.168.10.20"))
        self.assertFalse(_is_loopback_address("example.com"))

    def test_same_machine_lan_address_is_local(self) -> None:
        self.assertTrue(
            _is_local_dashboard_request("192.168.10.101", "192.168.10.101:6185")
        )
        self.assertFalse(
            _is_local_dashboard_request("192.168.10.20", "192.168.10.101:6185")
        )


class RoomLaunchApiTests(unittest.IsolatedAsyncioTestCase):
    def _plugin(self) -> TogetherCompanionPlugin:
        plugin = TogetherCompanionPlugin.__new__(TogetherCompanionPlugin)
        plugin.server_enabled = True
        plugin.room_server = SimpleNamespace(running=True)
        plugin.last_ticket_args = {}

        def issue_room_ticket(**kwargs):
            plugin.last_ticket_args = kwargs
            return RoomTicket(
                token="ticket",
                mode=kwargs["mode"],
                user_id=kwargs.get("user_id", ""),
                created_at=time.time(),
                expires_at=time.time() + 600,
            )

        plugin.issue_room_ticket = issue_room_ticket
        plugin._ticket_url = lambda ticket: f"http://127.0.0.1:6321/?ticket=ticket&mode={ticket.mode}"
        return plugin

    async def test_local_page_request_opens_default_browser(self) -> None:
        async def payload(*_args, **_kwargs):
            return {"mode": "call", "open_browser": True}

        request.json = payload
        request.client_host = "127.0.0.1"
        request.headers = {"host": "127.0.0.1:6185"}
        with patch("astrbot_plugin_together_companion.main.webbrowser.open", return_value=True) as opener:
            result = await self._plugin().page_create_room()

        self.assertTrue(result["data"]["browser_opened"])
        opener.assert_called_once_with(
            "http://127.0.0.1:6321/?ticket=ticket&mode=call",
            new=2,
            autoraise=True,
        )

    async def test_remote_page_request_does_not_open_server_browser(self) -> None:
        async def payload(*_args, **_kwargs):
            return {"mode": "call", "open_browser": True}

        request.json = payload
        request.client_host = "192.168.10.20"
        request.headers = {"host": "192.168.10.101:6185"}
        with patch("astrbot_plugin_together_companion.main.webbrowser.open") as opener:
            result = await self._plugin().page_create_room()

        self.assertFalse(result["data"]["browser_opened"])
        self.assertFalse(result["data"]["browser_launch_available"])
        opener.assert_not_called()

    async def test_llm_watch_tool_creates_ticket_and_opens_browser(self) -> None:
        plugin = self._plugin()
        event = SimpleNamespace(get_sender_id=lambda: "user-42")
        with patch("astrbot_plugin_together_companion.main.webbrowser.open", return_value=True) as opener:
            result = await plugin.open_together_watch_room_tool(event)

        self.assertIn("共同观影房间已准备好", result)
        self.assertIn("不会自动选择或播放视频", result)
        self.assertEqual({"mode": "watch", "user_id": "user-42"}, plugin.last_ticket_args)
        opener.assert_called_once_with(
            "http://127.0.0.1:6321/?ticket=ticket&mode=watch",
            new=2,
            autoraise=True,
        )


class PageConfigApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_page_provider_options_are_grouped_by_capability(self) -> None:
        class ChatProvider:
            provider_config = {"id": "vision-a", "modalities": ["text", "image"]}

            def meta(self):
                return {"model": "Vision A"}

            async def text_chat(self, **_kwargs):
                return None

        class SttProvider:
            provider_config = {"id": "stt-a"}

            async def get_text(self, _path):
                return ""

        class TtsProvider:
            provider_config = {"id": "tts-a"}

            async def get_audio(self, _text):
                return ""

        plugin = TogetherCompanionPlugin.__new__(TogetherCompanionPlugin)
        plugin.context = SimpleNamespace(
            get_all_providers=lambda: [ChatProvider()],
            get_all_stt_providers=lambda: [SttProvider()],
            get_all_tts_providers=lambda: [TtsProvider()],
            provider_manager=None,
        )

        options = await plugin._page_provider_options()

        self.assertEqual("vision-a", options["chat"][0]["id"])
        self.assertEqual("vision-a", options["vision"][0]["id"])
        self.assertEqual("stt-a", options["stt"][0]["id"])
        self.assertEqual("tts-a", options["tts"][0]["id"])

    async def test_page_save_config_persists_and_syncs_runtime(self) -> None:
        class Config(dict):
            def __init__(self):
                super().__init__({"conversation": {}, "speech": {}, "watch": {}})
                self.saved = 0

            def save_config(self):
                self.saved += 1

        async def payload(*_args, **_kwargs):
            return {
                "values": {
                    "conversation.history_turns": 8,
                    "speech.stt_mode": "browser",
                    "watch.comment_interval_seconds": 90,
                    "watch.scene_min_interval_seconds": 25,
                    "watch.duck_volume_percent": 24,
                    "server.port": 7000,
                }
            }

        plugin = TogetherCompanionPlugin.__new__(TogetherCompanionPlugin)
        plugin.config = Config()
        request.json = payload

        result = await plugin.page_save_config()

        self.assertEqual("ok", result["status"])
        self.assertTrue(result["data"]["persisted"])
        self.assertEqual(1, plugin.config.saved)
        self.assertEqual(8, plugin.history_turns)
        self.assertEqual("browser", plugin.stt_mode)
        self.assertEqual(90, plugin.watch_comment_interval_seconds)
        self.assertEqual(25, plugin.watch_scene_min_interval_seconds)
        self.assertEqual(0.24, plugin.watch_duck_volume_ratio)
        self.assertNotIn("server", plugin.config)

    def test_page_config_validation_clamps_numeric_values(self) -> None:
        plugin = TogetherCompanionPlugin.__new__(TogetherCompanionPlugin)
        plugin.config = {}

        updates = plugin._validate_page_settings(
            {
                "conversation.history_turns": -4,
                "watch.comment_interval_seconds": 9999,
                "watch.memory_refresh_seconds": 1,
                "speech.stt_mode": "unexpected",
                "unknown.key": "ignored",
            }
        )

        self.assertEqual(2, updates["conversation.history_turns"])
        self.assertEqual(600, updates["watch.comment_interval_seconds"])
        self.assertEqual(90, updates["watch.memory_refresh_seconds"])
        self.assertEqual("auto", updates["speech.stt_mode"])
        self.assertNotIn("unknown.key", updates)


if __name__ == "__main__":
    unittest.main()
