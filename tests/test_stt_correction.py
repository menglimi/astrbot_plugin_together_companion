# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from types import SimpleNamespace

from astrbot_stubs import install_astrbot_stubs

install_astrbot_stubs()

from astrbot_plugin_together_companion.main import TogetherCompanionPlugin
from astrbot_plugin_together_companion.models import RoomSession


class _CorrectionProvider:
    def __init__(self, completion: str) -> None:
        self.completion = completion
        self.calls = []

    async def text_chat(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(completion_text=self.completion)


class SttCorrectionTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _plugin(provider: _CorrectionProvider) -> TogetherCompanionPlugin:
        plugin = TogetherCompanionPlugin.__new__(TogetherCompanionPlugin)
        plugin.stt_correction_enabled = True
        plugin.token_usage = None
        plugin._get_chat_provider = lambda: provider
        plugin._bot_name = lambda: "诺星缘"
        plugin._companion_scene = lambda _user_id: {"relationship": {"name": "比折"}}
        return plugin

    async def test_bot_name_homophone_is_corrected_with_dynamic_context(self) -> None:
        provider = _CorrectionProvider('{"text":"诺星缘，你在吗","changed":true}')
        plugin = self._plugin(provider)
        room = RoomSession("room", "ticket", "call", "995051631", None)
        room.append_turn("assistant", "我在听", history_turns=6)

        corrected = await plugin._correct_stt_transcript(
            room,
            "诺星元你在吗",
            source="browser_stt",
            alternatives=["诺星缘你在吗", "落星元你在吗"],
        )

        self.assertEqual("诺星缘，你在吗", corrected)
        self.assertIn("诺星缘", provider.calls[0]["prompt"])
        self.assertIn("落星元你在吗", provider.calls[0]["prompt"])
        self.assertIn("无法确定是否误识别时，原样返回", provider.calls[0]["system_prompt"])

    async def test_uncertain_or_invalid_model_output_keeps_original(self) -> None:
        provider = _CorrectionProvider("这不是 JSON")
        plugin = self._plugin(provider)
        room = RoomSession("room", "ticket", "call", "995051631", None)

        corrected = await plugin._correct_stt_transcript(
            room,
            "星星很亮",
            source="astrbot_stt",
        )

        self.assertEqual("星星很亮", corrected)

    async def test_ordinary_speech_skips_correction_model(self) -> None:
        provider = _CorrectionProvider('{"text":"今晚吃什么","changed":false}')
        plugin = self._plugin(provider)
        room = RoomSession("room", "ticket", "call", "995051631", None)

        corrected = await plugin._correct_stt_transcript(
            room,
            "今晚吃什么",
            source="astrbot_stt",
        )

        self.assertEqual("今晚吃什么", corrected)
        self.assertEqual([], provider.calls)

    async def test_browser_redaction_is_restored_by_contextual_correction(self) -> None:
        provider = _CorrectionProvider('{"text":"笨蛋，该准备睡觉了。","changed":true}')
        plugin = self._plugin(provider)
        room = RoomSession("room", "ticket", "call", "995051631", None)
        room.call_active = True
        room.append_turn("assistant", "这么晚了，该休息啦。", history_turns=6)

        corrected = await plugin._correct_stt_transcript(
            room,
            "**，该准备睡觉了。",
            source="browser_stt",
        )

        self.assertEqual("笨蛋，该准备睡觉了。", corrected)
        self.assertEqual(1, len(provider.calls))
        self.assertIn("连续星号", provider.calls[0]["system_prompt"])
        self.assertIn('"has_redaction_marker": true', provider.calls[0]["prompt"])

    def test_redaction_detection_does_not_match_single_asterisk(self) -> None:
        self.assertTrue(
            TogetherCompanionPlugin._stt_contains_redaction_marker(
                "**，该准备睡觉了。",
                [],
            )
        )
        self.assertTrue(
            TogetherCompanionPlugin._stt_contains_redaction_marker(
                "＊＊，该准备睡觉了。",
                [],
            )
        )
        self.assertFalse(
            TogetherCompanionPlugin._stt_contains_redaction_marker(
                "价格是 5*2",
                [],
            )
        )

    async def test_luoxingyuan_candidate_triggers_name_correction(self) -> None:
        provider = _CorrectionProvider('{"text":"诺星缘，你在吗","changed":true}')
        plugin = self._plugin(provider)
        room = RoomSession("room", "ticket", "call", "995051631", None)

        corrected = await plugin._correct_stt_transcript(
            room,
            "落星缘，你在吗",
            source="astrbot_stt",
        )

        self.assertEqual("诺星缘，你在吗", corrected)
        self.assertEqual(1, len(provider.calls))

    async def test_repeated_name_transcript_uses_local_correction_cache(self) -> None:
        provider = _CorrectionProvider('{"text":"诺星缘，你在吗","changed":true}')
        plugin = self._plugin(provider)
        room = RoomSession("room", "ticket", "call", "995051631", None)

        first = await plugin._correct_stt_transcript(
            room,
            "落星缘，你在吗",
            source="browser_stt",
            alternatives=["诺星缘你在吗"],
        )
        second = await plugin._correct_stt_transcript(
            room,
            "落星缘，你在吗",
            source="browser_stt",
            alternatives=["诺星缘你在吗"],
        )

        self.assertEqual(first, second)
        self.assertEqual(1, len(provider.calls))

    async def test_disabled_correction_does_not_call_model(self) -> None:
        provider = _CorrectionProvider('{"text":"诺星缘","changed":true}')
        plugin = self._plugin(provider)
        plugin.stt_correction_enabled = False
        room = RoomSession("room", "ticket", "call", "995051631", None)

        corrected = await plugin._correct_stt_transcript(room, "诺星元", source="browser_stt")

        self.assertEqual("诺星元", corrected)
        self.assertEqual([], provider.calls)

    async def test_voice_reply_prompt_keeps_bot_name_interpretation_hint(self) -> None:
        provider = _CorrectionProvider("{}")
        plugin = self._plugin(provider)
        plugin.enable_memory_context = False
        plugin.custom_system_prompt = ""

        async def persona_prompt():
            return "你是诺星缘。"

        plugin._persona_prompt = persona_prompt
        room = RoomSession("room", "ticket", "call", "995051631", None)

        prompt = await plugin._build_system_prompt(
            room,
            query="诺星元你在吗",
            input_source="browser_stt",
        )

        self.assertIn("你的准确名称是“诺星缘”", prompt)
        self.assertIn("不要因为名字被误写而否认、反问或纠正用户", prompt)

    async def test_active_call_prompt_states_that_the_call_is_connected(self) -> None:
        provider = _CorrectionProvider("{}")
        plugin = self._plugin(provider)
        plugin.enable_memory_context = False
        plugin.custom_system_prompt = ""

        async def persona_prompt():
            return "你是诺星缘。"

        plugin._persona_prompt = persona_prompt
        room = RoomSession("room", "ticket", "call", "995051631", None)
        room.call_active = True

        prompt = await plugin._build_system_prompt(room, query="现在听得到吗")

        self.assertIn("已经接通实时语音通话", prompt)
        self.assertIn("不要声称自己只看得到文字", prompt)

    async def test_inactive_call_room_does_not_claim_microphone_audio(self) -> None:
        provider = _CorrectionProvider("{}")
        plugin = self._plugin(provider)
        plugin.enable_memory_context = False
        plugin.custom_system_prompt = ""

        async def persona_prompt():
            return "你是诺星缘。"

        plugin._persona_prompt = persona_prompt
        room = RoomSession("room", "ticket", "call", "995051631", None)

        prompt = await plugin._build_system_prompt(room, query="在吗")

        self.assertIn("语音通话尚未接通", prompt)
        self.assertNotIn("已经接通实时语音通话", prompt)


if __name__ == "__main__":
    unittest.main()
