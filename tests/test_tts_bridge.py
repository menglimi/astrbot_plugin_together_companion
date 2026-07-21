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


class TogetherTtsBridgeTests(unittest.IsolatedAsyncioTestCase):
    def _plugin(self, provider, api) -> TogetherCompanionPlugin:
        plugin = TogetherCompanionPlugin.__new__(TogetherCompanionPlugin)
        plugin._get_tts_provider = lambda: provider
        plugin._private_companion_api = lambda: api
        plugin.send_room_payload = AsyncMock()
        plugin._start_live_mouth_sync = AsyncMock()
        return plugin

    async def test_companion_bridge_audio_is_used_without_direct_provider_call(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_audio:
            temp_audio.write(b"audio")
            audio_path = temp_audio.name
        provider = SimpleNamespace(get_audio=AsyncMock())
        api = SimpleNamespace(
            synthesize_realtime_voice=AsyncMock(
                return_value={
                    "available": True,
                    "audio_path": audio_path,
                    "spoken_text": "一緒に見よう。",
                    "fallback_text": "一起看吧。",
                    "language": "ja-JP",
                }
            )
        )
        plugin = self._plugin(provider, api)
        room = RoomSession("room", "ticket", "watch", "123", None)
        try:
            await plugin._synthesize_and_send(
                room,
                "一起看吧。",
                display_text="一起看吧。",
                display_source="reply",
            )

            provider.get_audio.assert_not_awaited()
            api.synthesize_realtime_voice.assert_awaited_once()
            audio_payload = next(
                call.args[1]
                for call in plugin.send_room_payload.await_args_list
                if call.args[1].get("type") == "audio"
            )
            self.assertEqual("一緒に見よう。", audio_payload["text"])
            self.assertEqual("ja-JP", audio_payload["language"])
            self.assertEqual("一起看吧。", audio_payload["display_text"])
            self.assertEqual("reply", audio_payload["source"])
        finally:
            Path(audio_path).unlink(missing_ok=True)

    async def test_conversion_failure_uses_chinese_browser_fallback(self) -> None:
        provider = SimpleNamespace(get_audio=AsyncMock())
        api = SimpleNamespace(
            synthesize_realtime_voice=AsyncMock(
                return_value={
                    "available": True,
                    "audio_path": "",
                    "fallback_text": "一起看吧。",
                    "language": "zh-CN",
                    "reason": "language_conversion_failed",
                }
            )
        )
        plugin = self._plugin(provider, api)
        room = RoomSession("room", "ticket", "watch", "123", None)

        await plugin._synthesize_and_send(
            room,
            "一起看吧。",
            display_text="一起看吧。",
            display_source="watch_comment",
        )

        provider.get_audio.assert_not_awaited()
        fallback = plugin.send_room_payload.await_args_list[-1].args[1]
        self.assertEqual("tts_fallback", fallback["type"])
        self.assertEqual("zh-CN", fallback["language"])
        self.assertEqual("一起看吧。", fallback["display_text"])
        self.assertEqual("watch_comment", fallback["source"])

    async def test_local_playback_bridge_is_bypassed_to_keep_browser_as_only_output(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_audio:
            temp_audio.write(b"audio")
            audio_path = temp_audio.name
        provider = SimpleNamespace(get_audio=AsyncMock(return_value=audio_path))
        api = SimpleNamespace(
            _plugin=SimpleNamespace(
                enable_tts_local_playback=True,
                enable_tts_local_playback_live_only=False,
            ),
            synthesize_realtime_voice=AsyncMock(),
        )
        plugin = self._plugin(provider, api)
        room = RoomSession("room", "ticket", "watch", "123", None)
        try:
            await plugin._synthesize_and_send(
                room,
                "片尾到了。",
                display_text="片尾到了。",
                display_source="watch_comment",
            )

            api.synthesize_realtime_voice.assert_not_awaited()
            provider.get_audio.assert_awaited_once_with("片尾到了。")
            payload_types = [call.args[1].get("type") for call in plugin.send_room_payload.await_args_list]
            self.assertEqual(1, payload_types.count("audio"))
            self.assertNotIn("tts_fallback", payload_types)
        finally:
            Path(audio_path).unlink(missing_ok=True)

    async def test_bootstrap_uses_tts_language_independent_from_stt(self) -> None:
        plugin = TogetherCompanionPlugin.__new__(TogetherCompanionPlugin)
        plugin._capabilities = AsyncMock(
            return_value={
                "chat": {"available": True, "label": "chat"},
                "vision": {"available": True, "label": "vision"},
                "stt": {"available": True, "label": "stt"},
                "tts": {"available": True, "label": "tts"},
            }
        )
        plugin._companion_realtime_voice_config = lambda: {"browser_language": "ja-JP"}
        plugin._companion_scene = lambda user_id: {}
        plugin._bot_name = lambda: "Bot"
        plugin.stt_mode = "browser"
        plugin.browser_language = "zh-CN"
        plugin.browser_tts_fallback = True
        plugin.watch_auto_comment = True
        plugin.watch_comment_interval_seconds = 30
        plugin.watch_scene_min_interval_seconds = 8
        plugin.watch_duck_video_volume = True
        plugin.watch_duck_volume_ratio = 0.3
        room = RoomSession("room", "ticket", "watch", "123", None)

        bootstrap = await plugin.room_bootstrap(room)

        self.assertEqual("zh-CN", bootstrap["stt"]["browser_language"])
        self.assertEqual("ja-JP", bootstrap["tts"]["browser_language"])

    def test_browser_fallback_uses_tts_language(self) -> None:
        source = (
            Path(__file__).resolve().parents[1] / "web" / "app.js"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "speakInBrowser(message.text, message.language, message.display_text, message.source)",
            source,
        )
        self.assertIn(
            'utterance.lang = language || state.room?.tts?.browser_language || "zh-CN";',
            source,
        )
        self.assertNotIn(
            'utterance.lang = state.room?.stt?.browser_language || "zh-CN";',
            source,
        )

    def test_reply_text_is_revealed_by_actual_playback_events(self) -> None:
        source = (
            Path(__file__).resolve().parents[1] / "web" / "app.js"
        ).read_text(encoding="utf-8")

        self.assertIn('audio.addEventListener("playing", () => {', source)
        self.assertIn('utterance.addEventListener("start", () => {', source)
        self.assertIn("revealSpeechMessage(item);", source)
        self.assertIn("revealSpeechMessage(message);", source)

    def test_duplicate_watch_speech_is_filtered_before_both_playback_paths(self) -> None:
        source = (
            Path(__file__).resolve().parents[1] / "web" / "app.js"
        ).read_text(encoding="utf-8")

        self.assertIn("function isDuplicateWatchSpeech(message)", source)
        self.assertIn('message?.source !== "watch_comment"', source)
        self.assertEqual(2, source.count("if (isDuplicateWatchSpeech(message)) break;"))


if __name__ == "__main__":
    unittest.main()
