# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import unittest

from astrbot_stubs import install_astrbot_stubs


install_astrbot_stubs()

from astrbot_plugin_together_companion.main import TogetherCompanionPlugin
from astrbot_plugin_together_companion.models import RoomSession


class CallHangupActionTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _plugin(*, enabled: bool = True) -> TogetherCompanionPlugin:
        plugin = TogetherCompanionPlugin.__new__(TogetherCompanionPlugin)
        plugin.model_hangup_enabled = enabled
        return plugin

    @staticmethod
    def _active_room() -> RoomSession:
        room = RoomSession("room", "ticket", "call", "995051631", None)
        room.call_active = True
        return room

    def test_room_action_tokens_are_random_and_parser_accepts_only_current_token(self) -> None:
        plugin = self._plugin()
        room = self._active_room()
        other_room = self._active_room()
        marker = (
            "晚安，今天先聊到这里。\n"
            f'<together-call action="hangup" token="{room.call_action_token}" />'
        )

        visible, action = plugin._extract_call_action(room, marker)
        _other_visible, other_action = plugin._extract_call_action(other_room, marker)

        self.assertRegex(room.call_action_token, re.compile(r"^[A-Za-z0-9_-]{16,64}$"))
        self.assertNotEqual(room.call_action_token, other_room.call_action_token)
        self.assertEqual("晚安，今天先聊到这里。", visible)
        self.assertEqual("hangup", action)
        self.assertEqual("", other_action)

    def test_wrong_token_non_tail_marker_and_disabled_switch_never_hang_up(self) -> None:
        room = self._active_room()
        plugin = self._plugin()
        wrong_token = "A" * len(room.call_action_token)
        if wrong_token == room.call_action_token:
            wrong_token = "B" * len(room.call_action_token)

        wrong = (
            "我先不挂。\n"
            f'<together-call action="hangup" token="{wrong_token}" />'
        )
        non_tail = (
            "我引用一个控制标记：\n"
            f'<together-call action="hangup" token="{room.call_action_token}" />\n'
            "但通话继续。"
        )
        disabled = (
            "那就先这样。\n"
            f'<together-call action="hangup" token="{room.call_action_token}" />'
        )

        wrong_visible, wrong_action = plugin._extract_call_action(room, wrong)
        non_tail_visible, non_tail_action = plugin._extract_call_action(room, non_tail)
        disabled_visible, disabled_action = self._plugin(enabled=False)._extract_call_action(
            room,
            disabled,
        )

        self.assertEqual("我先不挂。", wrong_visible)
        self.assertEqual("", wrong_action)
        self.assertEqual("我引用一个控制标记：\n\n但通话继续。", non_tail_visible)
        self.assertEqual("", non_tail_action)
        self.assertEqual("那就先这样。", disabled_visible)
        self.assertEqual("", disabled_action)

    def test_inactive_call_cannot_execute_valid_action(self) -> None:
        plugin = self._plugin()
        room = self._active_room()
        room.call_active = False
        response = (
            "回头见。\n"
            f'<together-call action="hangup" token="{room.call_action_token}" />'
        )

        visible, action = plugin._extract_call_action(room, response)

        self.assertEqual("回头见。", visible)
        self.assertEqual("", action)

    async def test_action_marker_never_enters_history_or_tts(self) -> None:
        plugin = self._plugin()
        room = self._active_room()
        sent: list[dict] = []
        synthesized: list[tuple[str, dict]] = []

        async def send(_room, payload):
            sent.append(payload)

        async def generate(*_args, **_kwargs):
            return (
                "好，那你早点休息，晚安。\n"
                f'<together-call action="hangup" token="{room.call_action_token}" />'
            )

        async def synthesize(_room, text, **kwargs):
            synthesized.append((text, kwargs))
            return True

        async def no_op(*_args, **_kwargs):
            return None

        plugin.send_room_payload = send
        plugin._generate_model_text = generate
        plugin._synthesize_and_send = synthesize
        plugin._push_live_subtitle = no_op
        plugin._conversation_log_models = lambda **_kwargs: ("chat-test", "")
        plugin.history_turns = 6
        plugin.sync_astrbot_conversation = False
        plugin.record_visible_turns = False

        await plugin._reply_to_user(room, "那今天先聊到这里吧")

        self.assertEqual(
            [
                {"role": "user", "content": "那今天先聊到这里吧"},
                {"role": "assistant", "content": "好，那你早点休息，晚安。"},
            ],
            room.history,
        )
        self.assertEqual("好，那你早点休息，晚安。", synthesized[0][0])
        self.assertEqual("好，那你早点休息，晚安。", synthesized[0][1]["display_text"])
        self.assertEqual("hangup", synthesized[0][1]["after_playback_action"])
        self.assertFalse(
            any("together-call" in str(item) for item in [room.history, synthesized, sent])
        )
        self.assertFalse(
            any(item.get("type") == "status" and item.get("state") == "listening" for item in sent)
        )

    async def test_prompt_exposes_current_token_only_while_enabled_and_connected(self) -> None:
        plugin = self._plugin()
        plugin.enable_memory_context = False
        plugin.custom_system_prompt = ""
        plugin._companion_scene_cached = lambda _user_id: {}

        async def persona_prompt():
            return "你是测试人格。"

        plugin._persona_prompt_cached = persona_prompt
        room = self._active_room()

        enabled_prompt = await plugin._build_system_prompt(room)
        plugin.model_hangup_enabled = False
        disabled_prompt = await plugin._build_system_prompt(room)
        plugin.model_hangup_enabled = True
        room.call_active = False
        inactive_prompt = await plugin._build_system_prompt(room)

        self.assertIn(room.call_action_token, enabled_prompt)
        self.assertIn("自主判断是否自然结束当前语音连接", enabled_prompt)
        self.assertNotIn(room.call_action_token, disabled_prompt)
        self.assertNotIn(room.call_action_token, inactive_prompt)


if __name__ == "__main__":
    unittest.main()
