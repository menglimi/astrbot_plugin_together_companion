# -*- coding: utf-8 -*-
from __future__ import annotations

import time
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from astrbot_stubs import install_astrbot_stubs


install_astrbot_stubs()

from astrbot_plugin_together_companion.main import (
    TogetherCompanionPlugin,
    _is_local_dashboard_request,
    _is_loopback_address,
    request,
)
from astrbot_plugin_together_companion.models import RoomSession, RoomTicket


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
        self.assertIn('data-provider-kind="chat" required', page)
        self.assertIn("请选择对话模型（必选）", script)
        self.assertIn('name="watch.comment_interval_seconds"', page)
        self.assertIn('name="speech.realtime_duplex_enabled"', page)
        self.assertIn('name="speech.tts_timeout_seconds"', page)
        self.assertIn('role="switch"', page)
        self.assertIn('requestEndpoint("POST", "config/save"', script)
        self.assertNotIn('name="server.host"', page)
        self.assertNotIn('name="server.port"', page)

    def test_launcher_reports_tunnel_dns_readiness(self) -> None:
        script = (ROOT / "pages" / "一起房间" / "launcher.js").read_text(encoding="utf-8")

        self.assertIn("临时地址已分配，正在等待公网生效", script)
        self.assertIn("临时公网访问已生效", script)
        self.assertIn("window.setTimeout(loadStatus, 1800)", script)

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
        plugin.room_server = SimpleNamespace(running=True, local_base_url="http://127.0.0.1:6321")
        plugin.public_base_url = "https://together.example.com"
        plugin.quick_tunnel = SimpleNamespace(running=False, url="", start=AsyncMock())
        plugin.ticket_store = SimpleNamespace(revoke=Mock())
        plugin._get_chat_provider = lambda: SimpleNamespace(text_chat=lambda: None)
        plugin.last_ticket_args = {}
        plugin.ticket_calls = []

        def issue_room_ticket(**kwargs):
            plugin.last_ticket_args = kwargs
            plugin.ticket_calls.append(dict(kwargs))
            token = f"ticket-{len(plugin.ticket_calls)}"
            return RoomTicket(
                token=token,
                mode=kwargs["mode"],
                user_id=kwargs.get("user_id", ""),
                created_at=time.time(),
                expires_at=time.time() + 600,
            )

        plugin.issue_room_ticket = issue_room_ticket
        plugin._ticket_url = lambda ticket: f"http://127.0.0.1:6321/join/{ticket.token}?mode={ticket.mode}"
        return plugin

    async def test_local_page_request_opens_default_browser(self) -> None:
        async def payload(*_args, **_kwargs):
            return {"mode": "call", "open_browser": True}

        request.json = payload
        request.client_host = "127.0.0.1"
        request.headers = {"host": "127.0.0.1:6185"}
        plugin = self._plugin()
        with patch("astrbot_plugin_together_companion.main.webbrowser.open", return_value=True) as opener:
            result = await plugin.page_create_room()

        self.assertTrue(result["data"]["browser_opened"])
        self.assertEqual(
            "http://127.0.0.1:6321/join/ticket-2?mode=call",
            result["data"]["url"],
        )
        self.assertEqual(2, len(plugin.ticket_calls))
        opener.assert_called_once_with(
            "http://127.0.0.1:6321/join/ticket-1?mode=call",
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
        self.assertIn("/join/ticket-1?mode=call", result["data"]["url"])
        opener.assert_not_called()

    async def test_connected_room_can_issue_fresh_invite_ticket(self) -> None:
        plugin = self._plugin()
        plugin.send_room_payload = AsyncMock()
        room = RoomSession("room", "consumed-ticket", "call", "user-42", None)

        await plugin.handle_room_payload(room, {"type": "create_invite"})

        self.assertEqual({"mode": "call", "user_id": "user-42"}, plugin.last_ticket_args)
        payload = plugin.send_room_payload.await_args.args[1]
        self.assertEqual("invite_link", payload["type"])
        self.assertIn("/join/ticket-1?mode=call", payload["url"])

    async def test_llm_watch_tool_returns_mobile_link_without_opening_local_browser(self) -> None:
        plugin = self._plugin()
        event = SimpleNamespace(get_sender_id=lambda: "user-42")
        with patch("astrbot_plugin_together_companion.main.webbrowser.open", return_value=True) as opener:
            result = await plugin.open_together_watch_room_tool(event)

        payload = json.loads(result)
        self.assertIn("共同观影房间已准备好", payload["message"])
        self.assertIn("不会自动选择或播放视频", payload["message"])
        self.assertTrue(payload["credential_included"])
        self.assertTrue(payload["mobile_public_access"])
        self.assertEqual(
            "http://127.0.0.1:6321/join/ticket-1?mode=watch",
            payload["room_url"],
        )
        self.assertIn("逐字完整输出 room_url", payload["final_response_instruction"])
        self.assertEqual({"mode": "watch", "user_id": "user-42"}, plugin.last_ticket_args)
        self.assertEqual(1, len(plugin.ticket_calls))
        opener.assert_not_called()

    async def test_llm_tool_starts_quick_tunnel_and_returns_public_mobile_link(self) -> None:
        class QuickTunnel:
            def __init__(self):
                self.running = False
                self.url = ""
                self.local_url = ""
                self.start_calls = 0

            async def start(self, *, timeout):
                self.start_calls += 1
                self.running = True
                self.url = "https://mobile.trycloudflare.com"
                return self.url

            def status(self):
                return {"running": self.running, "ready": True, "url": self.url}

        plugin = self._plugin()
        plugin.public_base_url = ""
        plugin.quick_tunnel = QuickTunnel()
        plugin.__dict__.pop("_ticket_url", None)
        event = SimpleNamespace(get_sender_id=lambda: "user-42")

        with patch("astrbot_plugin_together_companion.main.webbrowser.open") as opener:
            payload = json.loads(await plugin.open_together_call_room_tool(event))

        self.assertEqual("ok", payload["status"])
        self.assertTrue(payload["tunnel_started"])
        self.assertTrue(payload["tunnel_ready"])
        self.assertEqual(1, plugin.quick_tunnel.start_calls)
        self.assertEqual("http://127.0.0.1:6321", plugin.quick_tunnel.local_url)
        self.assertEqual(
            "https://mobile.trycloudflare.com/join/ticket-1?mode=call",
            payload["room_url"],
        )
        opener.assert_not_called()

    async def test_together_command_starts_tunnel_and_sends_mobile_link(self) -> None:
        class QuickTunnel:
            running = False
            url = ""
            local_url = ""

            async def start(self, *, timeout):
                self.running = True
                self.url = "https://command.trycloudflare.com"
                return self.url

            def status(self):
                return {"running": self.running, "ready": True, "url": self.url}

        plugin = self._plugin()
        plugin.public_base_url = ""
        plugin.quick_tunnel = QuickTunnel()
        plugin.__dict__.pop("_ticket_url", None)
        event = SimpleNamespace(
            message_str="/一起",
            get_sender_id=lambda: "user-42",
            plain_result=lambda text: text,
        )

        messages = [message async for message in plugin.together_command(event)]

        self.assertEqual(1, len(messages))
        self.assertIn("已自动启动临时公网访问", messages[0])
        self.assertIn(
            "https://command.trycloudflare.com/join/ticket-1?mode=call",
            messages[0],
        )

    async def test_group_llm_tool_privately_sends_link_without_exposing_it_to_group(self) -> None:
        plugin = self._plugin()
        private_sender = AsyncMock(return_value={"message_id": 1})
        event = SimpleNamespace(
            get_sender_id=lambda: "10001",
            get_group_id=lambda: "20002",
            is_private_chat=lambda: False,
            bot=SimpleNamespace(send_private_msg=private_sender),
        )

        payload = json.loads(await plugin.open_together_call_room_tool(event))

        self.assertEqual("ok", payload["status"])
        self.assertTrue(payload["delivered_privately"])
        self.assertEqual("", payload["room_url"])
        self.assertNotIn("/join/", payload["message"])
        private_text = private_sender.await_args.kwargs["message"]
        self.assertIn("/join/ticket-1?mode=call", private_text)
        self.assertIn("请勿转发", private_text)

    async def test_group_command_privately_sends_link_and_only_confirms_in_group(self) -> None:
        plugin = self._plugin()
        private_sender = AsyncMock(return_value={"message_id": 1})
        event = SimpleNamespace(
            message_str="/一起",
            get_sender_id=lambda: "10001",
            get_group_id=lambda: "20002",
            is_private_chat=lambda: False,
            bot=SimpleNamespace(send_private_msg=private_sender),
            plain_result=lambda text: text,
        )

        messages = [message async for message in plugin.together_command(event)]

        self.assertEqual(["手机房间邀请链接已私发给你，请在私聊中打开。"], messages)
        self.assertNotIn("/join/", messages[0])
        self.assertIn("/join/ticket-1?mode=call", private_sender.await_args.kwargs["message"])

    async def test_group_delivery_failure_never_falls_back_to_public_link(self) -> None:
        plugin = self._plugin()
        event = SimpleNamespace(
            get_sender_id=lambda: "10001",
            get_group_id=lambda: "20002",
            is_private_chat=lambda: False,
            bot=SimpleNamespace(send_private_msg=AsyncMock(side_effect=RuntimeError("blocked"))),
        )

        payload = json.loads(await plugin.open_together_call_room_tool(event))

        self.assertEqual("error", payload["status"])
        self.assertTrue(payload["group_delivery_blocked"])
        self.assertNotIn("room_url", payload)
        plugin.ticket_store.revoke.assert_called_once_with("ticket-1")

    async def test_llm_tool_does_not_issue_ticket_without_chat_provider(self) -> None:
        plugin = self._plugin()
        plugin._get_chat_provider = lambda: None
        event = SimpleNamespace(get_sender_id=lambda: "user-42")

        with patch("astrbot_plugin_together_companion.main.webbrowser.open") as opener:
            payload = json.loads(await plugin.open_together_call_room_tool(event))

        self.assertEqual("error", payload["status"])
        self.assertIn("对话模型", payload["message"])
        self.assertEqual([], plugin.ticket_calls)
        opener.assert_not_called()


class TunnelPageApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_room_ticket_is_embedded_in_join_path(self) -> None:
        plugin = TogetherCompanionPlugin.__new__(TogetherCompanionPlugin)
        plugin.public_base_url = "https://together.example.com"
        plugin.quick_tunnel = SimpleNamespace(running=False, url="")
        plugin.room_server = SimpleNamespace(local_base_url="http://127.0.0.1:6321")
        ticket = RoomTicket("safe_token-123456789", "call", "user", time.time(), time.time() + 600)

        self.assertEqual(
            "https://together.example.com/join/safe_token-123456789?mode=call",
            plugin._ticket_url(ticket),
        )

    async def test_fixed_public_url_is_not_overridden(self) -> None:
        plugin = TogetherCompanionPlugin.__new__(TogetherCompanionPlugin)
        plugin.public_base_url = "https://together.example.com"
        plugin.quick_tunnel = SimpleNamespace(start=AsyncMock())

        result = await plugin.page_start_tunnel()

        self.assertEqual("error", result["status"])
        self.assertEqual("https://together.example.com", result["data"]["url"])
        plugin.quick_tunnel.start.assert_not_awaited()

    async def test_quick_tunnel_url_becomes_room_base_url(self) -> None:
        plugin = TogetherCompanionPlugin.__new__(TogetherCompanionPlugin)
        plugin.public_base_url = ""
        plugin.quick_tunnel = SimpleNamespace(running=True, url="https://quick.trycloudflare.com")
        plugin.room_server = SimpleNamespace(local_base_url="http://127.0.0.1:6321")

        self.assertEqual("https://quick.trycloudflare.com", plugin._room_base_url())


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
                    "conversation.chat_provider_id": "chat-a",
                    "conversation.history_turns": 8,
                    "speech.stt_mode": "browser",
                    "speech.tts_timeout_seconds": 75,
                    "speech.realtime_duplex_enabled": True,
                    "watch.comment_interval_seconds": 90,
                    "watch.scene_min_interval_seconds": 25,
                    "watch.duck_volume_percent": 24,
                    "server.port": 7000,
                }
            }

        plugin = TogetherCompanionPlugin.__new__(TogetherCompanionPlugin)
        plugin.config = Config()
        plugin.context = SimpleNamespace(
            get_provider_by_id=lambda provider_id: (
                SimpleNamespace(text_chat=lambda: None) if provider_id == "chat-a" else None
            )
        )
        request.json = payload

        result = await plugin.page_save_config()

        self.assertEqual("ok", result["status"])
        self.assertTrue(result["data"]["persisted"])
        self.assertEqual(1, plugin.config.saved)
        self.assertEqual(8, plugin.history_turns)
        self.assertEqual("browser", plugin.stt_mode)
        self.assertEqual(75, plugin.tts_timeout_seconds)
        self.assertTrue(plugin.realtime_duplex_enabled)
        self.assertEqual(90, plugin.watch_comment_interval_seconds)
        self.assertEqual(25, plugin.watch_scene_min_interval_seconds)
        self.assertEqual(0.24, plugin.watch_duck_volume_ratio)
        self.assertNotIn("server", plugin.config)

    async def test_page_save_rejects_missing_chat_provider(self) -> None:
        plugin = TogetherCompanionPlugin.__new__(TogetherCompanionPlugin)
        plugin.config = {}
        plugin.context = SimpleNamespace(get_provider_by_id=lambda _provider_id: None)
        plugin.chat_provider_id = ""

        async def payload(*_args, **_kwargs):
            return {"values": {"conversation.chat_provider_id": ""}}

        request.json = payload
        result = await plugin.page_save_config()

        self.assertEqual("error", result["status"])
        self.assertIn("对话模型", result["message"])

    def test_page_config_validation_clamps_numeric_values(self) -> None:
        plugin = TogetherCompanionPlugin.__new__(TogetherCompanionPlugin)
        plugin.config = {}

        updates = plugin._validate_page_settings(
            {
                "conversation.history_turns": -4,
                "watch.comment_interval_seconds": 9999,
                "watch.memory_refresh_seconds": 1,
                "speech.stt_mode": "unexpected",
                "speech.tts_timeout_seconds": 999,
                "speech.realtime_duplex_enabled": "true",
                "unknown.key": "ignored",
            }
        )

        self.assertEqual(2, updates["conversation.history_turns"])
        self.assertEqual(600, updates["watch.comment_interval_seconds"])
        self.assertEqual(90, updates["watch.memory_refresh_seconds"])
        self.assertEqual("auto", updates["speech.stt_mode"])
        self.assertEqual(180, updates["speech.tts_timeout_seconds"])
        self.assertTrue(updates["speech.realtime_duplex_enabled"])
        self.assertNotIn("unknown.key", updates)


if __name__ == "__main__":
    unittest.main()
