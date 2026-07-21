# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from types import SimpleNamespace

from astrbot_stubs import install_astrbot_stubs

install_astrbot_stubs()

from astrbot_plugin_together_companion.main import TogetherCompanionPlugin
from astrbot_plugin_together_companion.models import RoomSession


class _ConversationManager:
    def __init__(self, current_id: str = "", history=None) -> None:
        self.current_id = current_id
        self.history = history or []
        self.created = []
        self.pairs = []

    async def get_curr_conversation_id(self, unified_origin: str):
        return self.current_id if unified_origin == "default:FriendMessage:995051631" else None

    async def get_conversation(self, unified_origin: str, conversation_id: str):
        if unified_origin == "default:FriendMessage:995051631" and conversation_id == self.current_id:
            return SimpleNamespace(cid=conversation_id, history=self.history)
        return None

    async def new_conversation(self, unified_origin: str, platform_id: str, **kwargs):
        self.created.append((unified_origin, platform_id, kwargs))
        self.current_id = "created-conversation"
        return self.current_id

    async def add_message_pair(self, conversation_id: str, user_message: dict, assistant_message: dict):
        self.pairs.append((conversation_id, user_message, assistant_message))


class ConversationRecordingTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _plugin(manager: _ConversationManager) -> TogetherCompanionPlugin:
        plugin = TogetherCompanionPlugin.__new__(TogetherCompanionPlugin)
        plugin.context = SimpleNamespace(conversation_manager=manager, platform_manager=SimpleNamespace(platform_insts=[]))
        plugin.persona_id = ""
        return plugin

    async def test_visible_turn_is_appended_to_current_astrbot_conversation(self) -> None:
        manager = _ConversationManager("existing-conversation")
        plugin = self._plugin(manager)
        room = RoomSession("room", "ticket", "call", "995051631", None)

        recorded = await plugin._record_astrbot_turns(room, "Hello", "你好呀")

        self.assertTrue(recorded)
        self.assertEqual([], manager.created)
        self.assertEqual("existing-conversation", manager.pairs[0][0])
        self.assertEqual({"role": "user", "content": "Hello"}, manager.pairs[0][1])
        self.assertEqual({"role": "assistant", "content": "你好呀"}, manager.pairs[0][2])

    async def test_missing_astrbot_conversation_is_created_before_recording(self) -> None:
        manager = _ConversationManager()
        plugin = self._plugin(manager)
        room = RoomSession("room", "ticket", "call", "995051631", None)

        recorded = await plugin._record_astrbot_turns(room, "还在吗", "一直都在")

        self.assertTrue(recorded)
        self.assertEqual("default:FriendMessage:995051631", manager.created[0][0])
        self.assertEqual("default", manager.created[0][1])
        self.assertEqual("一起房间", manager.created[0][2]["title"])
        self.assertEqual("created-conversation", manager.pairs[0][0])

    async def test_existing_astrbot_history_is_loaded_into_new_room(self) -> None:
        manager = _ConversationManager(
            "existing-conversation",
            history=[
                {"role": "system", "content": "internal"},
                {"role": "user", "content": "之前我们聊到海边"},
                {"role": "assistant", "content": "我记得那片海很安静"},
                {"role": "tool", "content": "不要注入工具消息"},
            ],
        )
        plugin = self._plugin(manager)
        plugin.sync_astrbot_conversation = True
        plugin.history_turns = 12
        plugin._astrbot_conversation_platform_ids = lambda: ["default"]
        room = RoomSession("room", "ticket", "call", "995051631", None)

        await plugin._prime_astrbot_room_history(room)

        self.assertEqual(
            [
                {"role": "user", "content": "之前我们聊到海边"},
                {"role": "assistant", "content": "我记得那片海很安静"},
            ],
            room.history,
        )


if __name__ == "__main__":
    unittest.main()
