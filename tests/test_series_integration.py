# -*- coding: utf-8 -*-
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from astrbot_stubs import install_astrbot_stubs

install_astrbot_stubs()

from astrbot_plugin_together_companion.main import TogetherCompanionPlugin
from astrbot_plugin_together_companion.models import RoomSession
from astrbot_plugin_together_companion.server import TogetherRoomServer


class _MemoryBridge:
    def __init__(self) -> None:
        self.payloads = []

    async def record_shared_experience(self, **kwargs):
        self.payloads.append(kwargs)
        return kwargs["memory_id"]


class SeriesIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def _plugin(self) -> TogetherCompanionPlugin:
        plugin = TogetherCompanionPlugin.__new__(TogetherCompanionPlugin)
        plugin.record_shared_experiences = True
        plugin._companion_scene = lambda _user_id: {
            "relationship": {"name": "流星"},
        }
        plugin._bot_identity = lambda: {
            "name": "小星",
            "selected_id": "12345678",
            "qq_id": "12345678",
        }
        plugin._bot_name = lambda: "小星"
        return plugin

    def test_memory_context_contains_exact_bot_identity(self) -> None:
        plugin = self._plugin()
        room = RoomSession("room", "ticket", "call", "87654321", None)

        context = plugin._memory_session_context(room, "还在吗")

        self.assertEqual("12345678", context["bot_id"])
        self.assertEqual("小星", context["bot_name"])
        self.assertEqual("87654321", context["user_id"])

    async def test_bilibili_runtime_refresh_is_reused_before_media_resolution(self) -> None:
        plugin = self._plugin()
        runtime_cookie = {"value": "expired"}

        async def refresh_cookie():
            runtime_cookie["value"] = "refreshed"
            return True, "refreshed"

        runtime = SimpleNamespace(
            check_cookie=AsyncMock(return_value=(False, "expired")),
            refresh_cookie=AsyncMock(side_effect=refresh_cookie),
            _headers=lambda: {
                "Cookie": f"SESSDATA={runtime_cookie['value']}; buvid4=device-4",
                "User-Agent": "Chrome/120",
                "Referer": "https://www.bilibili.com",
            },
        )
        plugin.context = SimpleNamespace(
            get_registered_star=lambda name: (
                SimpleNamespace(star_cls=runtime)
                if name == "astrbot_plugin_bilibili_ai_bot"
                else None
            )
        )
        plugin._bilibili_runtime_state = {"at": 0.0, "linked": False, "valid": None}

        first = await plugin._sync_bilibili_bot_cookie()
        second = await plugin._sync_bilibili_bot_cookie()

        self.assertTrue(first["linked"])
        self.assertTrue(first["valid"])
        self.assertTrue(first["refreshed"])
        self.assertEqual("SESSDATA=refreshed; buvid4=device-4", first["headers"]["Cookie"])
        self.assertEqual(first, second)
        runtime.check_cookie.assert_awaited_once()
        runtime.refresh_cookie.assert_awaited_once()

    def test_manual_bot_qq_override_resolves_ambiguous_identity(self) -> None:
        plugin = TogetherCompanionPlugin.__new__(TogetherCompanionPlugin)
        plugin.bot_qq_id = "99887766"
        plugin._private_companion_api = lambda: type(
            "API",
            (),
            {
                "get_bot_identity": staticmethod(
                    lambda: {
                        "name": "小星",
                        "self_ids": ["12345678", "22345678"],
                        "selected_id": "",
                        "qq_id": "",
                        "ambiguous": True,
                    }
                )
            },
        )()

        identity = plugin._bot_identity()

        self.assertEqual("99887766", identity["selected_id"])
        self.assertEqual("99887766", identity["qq_id"])
        self.assertFalse(identity["ambiguous"])

    def test_connected_onebot_account_replaces_internal_platform_id(self) -> None:
        plugin = TogetherCompanionPlugin.__new__(TogetherCompanionPlugin)
        plugin.bot_qq_id = ""
        plugin.context = SimpleNamespace(
            platform_manager=SimpleNamespace(
                platform_insts=[
                    SimpleNamespace(
                        bot=SimpleNamespace(_wsr_api_clients={"3491542998": object()})
                    )
                ]
            )
        )
        plugin._private_companion_api = lambda: SimpleNamespace(
            get_bot_identity=lambda: {
                "name": "小星",
                "self_ids": ["d9484e4a31d74195a18498fd9e740beb"],
                "selected_id": "d9484e4a31d74195a18498fd9e740beb",
                "qq_id": "",
                "ambiguous": False,
            }
        )

        identity = plugin._bot_identity()

        self.assertEqual("3491542998", identity["selected_id"])
        self.assertEqual("3491542998", identity["qq_id"])
        self.assertFalse(identity["ambiguous"])

    def test_multiple_connected_onebot_accounts_are_not_guessed(self) -> None:
        plugin = TogetherCompanionPlugin.__new__(TogetherCompanionPlugin)
        plugin.bot_qq_id = ""
        plugin.context = SimpleNamespace(
            platform_manager=SimpleNamespace(
                platform_insts=[
                    SimpleNamespace(
                        bot=SimpleNamespace(
                            _wsr_api_clients={
                                "12345678": object(),
                                "22345678": object(),
                            }
                        )
                    )
                ]
            )
        )
        plugin._private_companion_api = lambda: None

        identity = plugin._bot_identity()

        self.assertEqual("", identity["qq_id"])
        self.assertTrue(identity["ambiguous"])

    async def test_call_summary_is_written_once_as_shared_experience(self) -> None:
        plugin = self._plugin()
        bridge = _MemoryBridge()
        plugin._memory_bridge = lambda: bridge

        async def decide(_material):
            return '{"remember":true,"summary":"我和流星聊了接下来一起看的影片。","reason":"有具体共同话题"}'

        plugin._generate_shared_experience_decision = decide
        room = RoomSession("room", "ticket", "call", "87654321", None)
        room.append_turn("user", "等会一起看那部影片吧", history_turns=12)
        room.append_turn("assistant", "好，我也正想和你一起看", history_turns=12)

        first = await plugin._record_shared_experience(room)
        second = await plugin._record_shared_experience(room)

        self.assertTrue(first)
        self.assertEqual("", second)
        self.assertEqual(1, len(bridge.payloads))
        self.assertEqual("12345678", bridge.payloads[0]["bot_id"])
        self.assertEqual("87654321", bridge.payloads[0]["user_id"])
        self.assertEqual("call", bridge.payloads[0]["experience_type"])

    async def test_cached_qq_avatar_is_preferred_without_network_request(self) -> None:
        plugin = self._plugin()
        plugin.bot_qq_id = ""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plugin._avatar_cache_dir = root / "avatar"
            plugin._avatar_cache_dir.mkdir()
            plugin.plugin_root = root
            avatar = plugin._avatar_cache_dir / "qq-12345678.jpg"
            avatar.write_bytes(b"cached")

            resolved = await plugin.resolve_avatar_path()

        self.assertEqual(avatar, resolved)

    def test_cached_avatar_content_type_uses_file_signature(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            avatar = Path(temp) / "avatar.jpg"
            avatar.write_bytes(b"\x89PNG\r\n\x1a\nrest")

            content_type = TogetherRoomServer._avatar_content_type(avatar)

        self.assertEqual("image/png", content_type)


if __name__ == "__main__":
    unittest.main()
