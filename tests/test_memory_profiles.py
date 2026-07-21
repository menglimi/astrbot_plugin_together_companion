# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from astrbot_stubs import install_astrbot_stubs

install_astrbot_stubs()

from astrbot_plugin_together_companion.main import TogetherCompanionPlugin
from astrbot_plugin_together_companion.models import RoomSession


class _MemoryBridge:
    def __init__(self, result: str = "一段相关记忆") -> None:
        self.result = result
        self.calls = []

    async def compose_context(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


class MemoryProfileTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _plugin(bridge: _MemoryBridge) -> TogetherCompanionPlugin:
        plugin = TogetherCompanionPlugin.__new__(TogetherCompanionPlugin)
        plugin._memory_bridge = lambda: bridge
        plugin._companion_scene = lambda _user_id: {"relationship": {"name": "比折"}}
        plugin._bot_identity = lambda: {"name": "诺星缘", "selected_id": "123", "qq_id": "123"}
        return plugin

    async def test_passive_reply_uses_small_high_relevance_budget(self) -> None:
        bridge = _MemoryBridge()
        plugin = self._plugin(bridge)
        room = RoomSession("room", "ticket", "call", "995051631", None)

        result = await plugin._memory_context(room, "今天有点累")

        self.assertEqual("一段相关记忆", result)
        self.assertEqual(2, bridge.calls[0]["top_k"])
        self.assertEqual(360, bridge.calls[0]["max_chars"])
        self.assertEqual("今天有点累", bridge.calls[0]["query"])

    async def test_proactive_comment_uses_broader_association_budget(self) -> None:
        bridge = _MemoryBridge("共同看过类似的桥段")
        plugin = self._plugin(bridge)
        room = RoomSession("room", "ticket", "watch", "995051631", None)

        result = await plugin._memory_context(room, "画面里的人突然摔倒", proactive=True)

        self.assertEqual("共同看过类似的桥段", result)
        self.assertEqual(8, bridge.calls[0]["top_k"])
        self.assertEqual(1800, bridge.calls[0]["max_chars"])
        self.assertIn("主动联想", bridge.calls[0]["query"])
        self.assertIn("当前观影线索：画面里的人突然摔倒", bridge.calls[0]["query"])

    async def test_watch_comment_labels_memory_as_optional_inspiration(self) -> None:
        bridge = _MemoryBridge("以前也一起吐槽过类似场面")
        plugin = self._plugin(bridge)
        plugin.enable_memory_context = True
        plugin.custom_system_prompt = ""
        plugin.persona_id = ""
        plugin._persona_cache = {"at": 0.0, "key": "", "prompt": ""}
        plugin._scene_cache = {"at": 0.0, "user_id": "", "scene": {}}
        plugin._identity_cache = {"at": 0.0, "identity": {}}

        async def persona_prompt():
            return "你是诺星缘。"

        plugin._persona_prompt = persona_prompt
        plugin._companion_scene_cached = lambda _user_id: {}
        room = RoomSession("room", "ticket", "watch", "995051631", None)

        prompt = await plugin._build_system_prompt(
            room,
            query="画面中的人物突然摔倒",
            watch_comment=True,
        )

        self.assertIn("可用于主动联想的相关共同记忆", prompt)
        self.assertIn("仅作灵感，不必提及", prompt)
        self.assertIn("以前也一起吐槽过类似场面", prompt)

    async def test_call_proactive_uses_broad_memory_as_optional_inspiration(self) -> None:
        bridge = _MemoryBridge("之前约好周末一起散步")
        plugin = self._plugin(bridge)
        plugin.enable_memory_context = True
        plugin.custom_system_prompt = ""
        plugin.persona_id = ""
        plugin._persona_cache = {"at": 0.0, "key": "", "prompt": ""}
        plugin._companion_scene_cached = lambda _user_id: {}

        async def persona_prompt():
            return "你是诺星缘。"

        plugin._persona_prompt = persona_prompt
        room = RoomSession("room", "ticket", "call", "995051631", None)

        prompt = await plugin._build_system_prompt(
            room,
            query="用户已经安静约 120 秒",
            call_proactive=True,
        )

        self.assertIn("语音通话中的一次内部主动开口判断", prompt)
        self.assertIn("之前约好周末一起散步", prompt)
        self.assertEqual(8, bridge.calls[0]["top_k"])


if __name__ == "__main__":
    unittest.main()
