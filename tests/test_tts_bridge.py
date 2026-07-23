# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from astrbot_stubs import install_astrbot_stubs


install_astrbot_stubs()

from astrbot_plugin_together_companion.main import BASE_REALTIME_PROMPT, TogetherCompanionPlugin
from astrbot_plugin_together_companion.models import RoomSession


class TogetherTtsBridgeTests(unittest.IsolatedAsyncioTestCase):
    def _plugin(self, provider, api) -> TogetherCompanionPlugin:
        plugin = TogetherCompanionPlugin.__new__(TogetherCompanionPlugin)
        plugin._get_tts_provider = lambda: provider
        plugin._private_companion_api = lambda: api
        plugin.send_room_payload = AsyncMock()
        plugin._start_live_mouth_sync = AsyncMock()
        return plugin

    def test_realtime_prompt_delegates_tts_markup_to_postprocessing(self) -> None:
        self.assertIn("独立处理语种转换和语音合成", BASE_REALTIME_PROMPT)
        self.assertIn("不要输出 <pc_tts>、<tts>", BASE_REALTIME_PROMPT)
        self.assertIn("只有后续系统提示明确启用", BASE_REALTIME_PROMPT)

    def test_connected_call_enables_direct_foreign_speech_from_new_voice_config(self) -> None:
        plugin = TogetherCompanionPlugin.__new__(TogetherCompanionPlugin)
        plugin.direct_multilingual_tts = True
        plugin._companion_realtime_voice_config = lambda: {
            "available": True,
            "voice_language": "ja",
            "browser_language": "ja-JP",
        }
        room = RoomSession("room", "ticket", "call", "123", None)
        room.call_active = True

        prompt = plugin._call_direct_speech_prompt(room)

        self.assertIn("通话外语语音直出", prompt)
        self.assertIn("日语（ja-JP）", prompt)
        self.assertIn("<pc_tts>", prompt)
        self.assertIn("自动回退到原有语种转换链路", prompt)

    def test_old_voice_config_and_plain_reply_keep_compatibility_fallbacks(self) -> None:
        plugin = TogetherCompanionPlugin.__new__(TogetherCompanionPlugin)
        plugin.direct_multilingual_tts = True
        plugin._companion_realtime_voice_config = lambda: {"browser_language": "en-GB"}
        room = RoomSession("room", "ticket", "call", "123", None)
        room.call_active = True

        self.assertIn("英语（en-GB）", plugin._call_direct_speech_prompt(room))
        room.call_active = False
        self.assertEqual("", plugin._call_direct_speech_prompt(room))
        room.call_active = True
        plugin.direct_multilingual_tts = False
        self.assertEqual("", plugin._call_direct_speech_prompt(room))

        spoken, visible = plugin._split_tts_payload("继续使用原有中文回复。")
        self.assertEqual("继续使用原有中文回复。", spoken)
        self.assertEqual("继续使用原有中文回复。", visible)

    def test_unavailable_or_chinese_voice_config_keeps_original_pipeline(self) -> None:
        plugin = TogetherCompanionPlugin.__new__(TogetherCompanionPlugin)
        plugin.direct_multilingual_tts = True
        room = RoomSession("room", "ticket", "call", "123", None)
        room.call_active = True

        plugin._companion_realtime_voice_config = lambda: {"available": False}
        self.assertEqual("", plugin._call_direct_speech_prompt(room))

        plugin._companion_realtime_voice_config = lambda: {
            "voice_language": "zh",
            "browser_language": "zh-CN",
        }
        self.assertEqual("", plugin._call_direct_speech_prompt(room))

        def broken_config():
            raise RuntimeError("old companion API failed")

        plugin._companion_realtime_voice_config = broken_config
        self.assertEqual("", plugin._call_direct_speech_prompt(room))

    async def test_connected_call_system_prompt_includes_direct_speech_contract(self) -> None:
        plugin = TogetherCompanionPlugin.__new__(TogetherCompanionPlugin)
        plugin.direct_multilingual_tts = True
        plugin.model_hangup_enabled = False
        plugin.custom_system_prompt = ""
        plugin.enable_memory_context = False
        plugin._companion_realtime_voice_config = lambda: {
            "available": True,
            "voice_language": "ja",
            "browser_language": "ja-JP",
        }
        plugin._companion_scene_cached = lambda _user_id: {}

        async def persona_prompt():
            return "你是测试人格。"

        plugin._persona_prompt_cached = persona_prompt
        room = RoomSession("room", "ticket", "call", "123", None)
        room.call_active = True

        prompt = await plugin._build_system_prompt(room)

        self.assertIn("已经接通实时语音通话", prompt)
        self.assertIn("通话外语语音直出", prompt)
        self.assertIn("<pc_tts>适合直接朗读的日语口语</pc_tts>用户可见正文", prompt)

        proactive_prompt = await plugin._build_system_prompt(
            room,
            query="用户已经安静约 120 秒",
            call_proactive=True,
        )
        self.assertIn("只输出一个 JSON 对象", proactive_prompt)
        self.assertNotIn("通话外语语音直出，TTS 目标语种", proactive_prompt)

    async def test_call_prompt_prefers_fresh_client_local_time(self) -> None:
        plugin = TogetherCompanionPlugin.__new__(TogetherCompanionPlugin)
        plugin.direct_multilingual_tts = False
        plugin.model_hangup_enabled = False
        plugin.custom_system_prompt = ""
        plugin.enable_memory_context = False
        plugin._companion_scene_cached = lambda _user_id: {
            "date": "2026-07-24",
            "time": "02:55",
        }

        async def persona_prompt():
            return "你是测试人格。"

        plugin._persona_prompt_cached = persona_prompt
        room = RoomSession("room", "ticket", "call", "123", None)
        client_now = datetime.now(timezone(timedelta(hours=8))).replace(microsecond=0)
        plugin._update_client_time_context(
            room,
            {
                "client_local_time": client_now.isoformat(),
                "client_timezone": "Asia/Shanghai",
            },
        )

        prompt = await plugin._build_system_prompt(room)

        self.assertIn(client_now.isoformat(), prompt)
        self.assertIn("Asia/Shanghai", prompt)
        self.assertIn("以这里的时间为准", prompt)
        self.assertIn("2026-07-24 02:55", prompt)

    async def test_missing_or_untrusted_client_time_uses_soft_uncertainty_prompt(self) -> None:
        plugin = TogetherCompanionPlugin.__new__(TogetherCompanionPlugin)
        plugin.direct_multilingual_tts = False
        plugin.model_hangup_enabled = False
        plugin.custom_system_prompt = ""
        plugin.enable_memory_context = False
        plugin._companion_scene_cached = lambda _user_id: {}

        async def persona_prompt():
            return "你是测试人格。"

        plugin._persona_prompt_cached = persona_prompt
        room = RoomSession("room", "ticket", "call", "123", None)
        plugin._update_client_time_context(
            room,
            {
                "client_local_time": "2000-01-01T03:00:00+08:00",
                "client_timezone": "<invalid>",
            },
        )

        prompt = await plugin._build_system_prompt(room)

        self.assertEqual("", room.client_local_time)
        self.assertIn("没有收到可确认的用户设备本地时间", prompt)
        self.assertIn("不要自行猜测具体几点", prompt)

    def test_voice_only_markup_has_clean_visible_fallback(self) -> None:
        spoken, visible = TogetherCompanionPlugin._split_tts_payload(
            "<pc_tts>[softly laughing]えへへ、本当に言うこと聞くね。</pc_tts>"
        )

        self.assertEqual("[softly laughing]えへへ、本当に言うこと聞くね。", spoken)
        self.assertEqual("えへへ、本当に言うこと聞くね。", visible)

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
        room = RoomSession("room", "ticket", "call", "123", None)
        try:
            with self.assertLogs("together-tests", level="INFO") as captured:
                await plugin._synthesize_and_send(
                    room,
                    "<pc_tts>一緒に見よう。</pc_tts>一起看吧。",
                    display_text="一起看吧。",
                    display_source="reply",
                )

            provider.get_audio.assert_not_awaited()
            api.synthesize_realtime_voice.assert_awaited_once_with(
                "一緒に見よう。",
                tts_provider=provider,
                source="together_companion",
                play_local=False,
            )
            log_text = "\n".join(captured.output)
            self.assertIn("text_source=llm_direct", log_text)
            self.assertIn("spoken_chars=7", log_text)
            self.assertIn("visible_chars=5", log_text)
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

    async def test_disabled_watch_tts_sends_text_without_synthesis(self) -> None:
        provider = SimpleNamespace(get_audio=AsyncMock())
        api = SimpleNamespace(synthesize_realtime_voice=AsyncMock())
        plugin = self._plugin(provider, api)
        room = RoomSession("room", "ticket", "watch", "123", None)
        room.watch_tts_enabled = False

        await plugin._synthesize_and_send(
            room,
            "一起看吧。",
            display_text="一起看吧。",
            display_source="watch_comment",
        )

        provider.get_audio.assert_not_awaited()
        api.synthesize_realtime_voice.assert_not_awaited()
        self.assertEqual(1, plugin.send_room_payload.await_count)
        payload = plugin.send_room_payload.await_args.args[1]
        self.assertEqual("bot_text", payload["type"])
        self.assertEqual("watch_comment", payload["source"])

    async def test_private_tts_markup_is_split_between_speech_and_display(self) -> None:
        provider = SimpleNamespace(get_audio=AsyncMock())
        api = SimpleNamespace(
            synthesize_realtime_voice=AsyncMock(
                return_value={
                    "available": False,
                    "audio_path": "",
                    "spoken_text": "うん、分かった。",
                    "fallback_text": "うん、分かった。",
                    "language": "ja-JP",
                }
            )
        )
        plugin = self._plugin(provider, api)
        room = RoomSession("room", "ticket", "watch", "123", None)

        await plugin._synthesize_and_send(
            room,
            "<pc_tts>[soft]うん、分かった。</pc_tts>嗯，知道啦。",
            display_source="reply",
        )

        api.synthesize_realtime_voice.assert_awaited_once_with(
            "[soft]うん、分かった。",
            tts_provider=provider,
            source="together_companion",
            play_local=False,
        )
        payload = plugin.send_room_payload.await_args_list[-1].args[1]
        self.assertEqual("tts_fallback", payload["type"])
        self.assertEqual("嗯，知道啦。", payload["display_text"])
        self.assertNotIn("pc_tts", payload["display_text"])

    async def test_disabled_watch_tts_hides_private_markup(self) -> None:
        provider = SimpleNamespace(get_audio=AsyncMock())
        api = SimpleNamespace(synthesize_realtime_voice=AsyncMock())
        plugin = self._plugin(provider, api)
        room = RoomSession("room", "ticket", "watch", "123", None)
        room.watch_tts_enabled = False

        await plugin._synthesize_and_send(
            room,
            "<pc_tts>[whispering]聞こえてるよ。</pc_tts>一直都听得到哦。",
            display_source="reply",
        )

        payload = plugin.send_room_payload.await_args.args[1]
        self.assertEqual("bot_text", payload["type"])
        self.assertEqual("一直都听得到哦。", payload["text"])
        provider.get_audio.assert_not_awaited()
        api.synthesize_realtime_voice.assert_not_awaited()

    async def test_watch_tts_toggle_updates_room_and_acknowledges(self) -> None:
        plugin = TogetherCompanionPlugin.__new__(TogetherCompanionPlugin)
        plugin.send_room_payload = AsyncMock()
        room = RoomSession("room", "ticket", "watch", "123", None)

        await plugin.handle_room_payload(
            room,
            {"type": "set_watch_tts", "enabled": False},
        )

        self.assertFalse(room.watch_tts_enabled)
        plugin.send_room_payload.assert_awaited_once_with(
            room,
            {"type": "watch_tts", "enabled": False},
        )

    async def test_conversion_failure_displays_text_without_chinese_speech(self) -> None:
        provider = SimpleNamespace(get_audio=AsyncMock())
        api = SimpleNamespace(
            synthesize_realtime_voice=AsyncMock(
                return_value={
                    "available": True,
                    "audio_path": "",
                    "fallback_text": "",
                    "language": "ja-JP",
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
        payload = plugin.send_room_payload.await_args_list[-1].args[1]
        self.assertEqual("bot_text", payload["type"])
        self.assertEqual("一起看吧。", payload["text"])
        self.assertEqual("watch_comment", payload["source"])
        self.assertNotIn(
            "tts_fallback",
            [call.args[1].get("type") for call in plugin.send_room_payload.await_args_list],
        )

    async def test_local_playback_bridge_is_called_with_local_output_disabled(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_audio:
            temp_audio.write(b"audio")
            audio_path = temp_audio.name
        provider = SimpleNamespace(get_audio=AsyncMock())
        api = SimpleNamespace(
            _plugin=SimpleNamespace(
                enable_tts_local_playback=True,
                enable_tts_local_playback_live_only=False,
            ),
            synthesize_realtime_voice=AsyncMock(
                return_value={
                    "available": True,
                    "audio_path": audio_path,
                    "spoken_text": "エンディングだよ。",
                    "fallback_text": "エンディングだよ。",
                    "language": "ja-JP",
                }
            ),
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

            api.synthesize_realtime_voice.assert_awaited_once_with(
                "片尾到了。",
                tts_provider=provider,
                source="together_companion",
                play_local=False,
            )
            provider.get_audio.assert_not_awaited()
            payload_types = [call.args[1].get("type") for call in plugin.send_room_payload.await_args_list]
            self.assertEqual(1, payload_types.count("audio"))
            self.assertNotIn("tts_fallback", payload_types)
        finally:
            Path(audio_path).unlink(missing_ok=True)

    async def test_legacy_bridge_without_play_local_remains_compatible(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_audio:
            temp_audio.write(b"audio")
            audio_path = temp_audio.name

        class LegacyApi:
            def __init__(self) -> None:
                self.calls = []

            async def synthesize_realtime_voice(
                self,
                text,
                *,
                tts_provider=None,
                source="external_realtime",
            ):
                self.calls.append((text, tts_provider, source))
                return {
                    "available": True,
                    "audio_path": audio_path,
                    "spoken_text": "一緒に見よう。",
                    "fallback_text": "一緒に見よう。",
                    "language": "ja-JP",
                }

        provider = SimpleNamespace(get_audio=AsyncMock())
        api = LegacyApi()
        plugin = self._plugin(provider, api)
        room = RoomSession("room", "ticket", "watch", "123", None)
        try:
            await plugin._synthesize_and_send(
                room,
                "一起看吧。",
                display_text="一起看吧。",
                display_source="reply",
            )

            self.assertEqual(
                [("一起看吧。", provider, "together_companion")],
                api.calls,
            )
            payload_types = [
                call.args[1].get("type")
                for call in plugin.send_room_payload.await_args_list
            ]
            self.assertIn("audio", payload_types)
            self.assertNotIn("bot_text", payload_types)
        finally:
            Path(audio_path).unlink(missing_ok=True)

    async def test_proxy_bridge_retries_and_caches_play_local_incompatibility(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_audio:
            temp_audio.write(b"audio")
            audio_path = temp_audio.name

        class ProxyApi:
            def __init__(self) -> None:
                self.calls = []

            async def synthesize_realtime_voice(self, text, **kwargs):
                self.calls.append(dict(kwargs))
                if "play_local" in kwargs:
                    raise TypeError("legacy helper got an unexpected keyword argument 'play_local'")
                return {
                    "available": True,
                    "audio_path": audio_path,
                    "spoken_text": "分かった。",
                    "fallback_text": "分かった。",
                    "language": "ja-JP",
                }

        provider = SimpleNamespace(get_audio=AsyncMock())
        api = ProxyApi()
        plugin = self._plugin(provider, api)
        room = RoomSession("room", "ticket", "call", "123", None)
        try:
            await plugin._synthesize_and_send(room, "知道啦。", display_text="知道啦。")
            await plugin._synthesize_and_send(room, "明白啦。", display_text="明白啦。")

            self.assertEqual(3, len(api.calls))
            self.assertIn("play_local", api.calls[0])
            self.assertNotIn("play_local", api.calls[1])
            self.assertNotIn("play_local", api.calls[2])
            self.assertFalse(plugin._tts_bridge_play_local_supported)
        finally:
            Path(audio_path).unlink(missing_ok=True)

    async def test_cancelled_tts_reveals_completed_reply_text(self) -> None:
        started = asyncio.Event()

        async def wait_forever(*_args, **_kwargs):
            started.set()
            await asyncio.Event().wait()

        provider = SimpleNamespace(get_audio=AsyncMock())
        api = SimpleNamespace(synthesize_realtime_voice=wait_forever)
        plugin = self._plugin(provider, api)
        room = RoomSession("room", "ticket", "call", "123", None)
        task = asyncio.create_task(
            plugin._synthesize_and_send(
                room,
                "已经生成的回复。",
                display_text="已经生成的回复。",
                display_source="reply",
            )
        )
        await started.wait()
        task.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await task

        payload_types = [call.args[1].get("type") for call in plugin.send_room_payload.await_args_list]
        self.assertIn("bot_text", payload_types)
        self.assertEqual("已经生成的回复。", plugin.send_room_payload.await_args_list[-1].args[1]["text"])

    async def test_missing_provider_uses_converted_browser_fallback(self) -> None:
        api = SimpleNamespace(
            synthesize_realtime_voice=AsyncMock(
                return_value={
                    "available": False,
                    "audio_path": "",
                    "spoken_text": "一緒に見よう。",
                    "fallback_text": "一緒に見よう。",
                    "language": "ja-JP",
                    "reason": "tts_provider_unavailable",
                }
            )
        )
        plugin = self._plugin(None, api)
        room = RoomSession("room", "ticket", "watch", "123", None)

        await plugin._synthesize_and_send(
            room,
            "一起看吧。",
            display_text="一起看吧。",
            display_source="reply",
        )

        api.synthesize_realtime_voice.assert_awaited_once_with(
            "一起看吧。",
            tts_provider=None,
            source="together_companion",
            play_local=False,
        )
        fallback = plugin.send_room_payload.await_args_list[-1].args[1]
        self.assertEqual("tts_fallback", fallback["type"])
        self.assertEqual("一緒に見よう。", fallback["text"])
        self.assertEqual("ja-JP", fallback["language"])
        self.assertEqual("一起看吧。", fallback["display_text"])

    async def test_bootstrap_uses_tts_language_independent_from_stt(self) -> None:
        plugin = TogetherCompanionPlugin.__new__(TogetherCompanionPlugin)
        plugin._capabilities = AsyncMock(
            return_value={
                "chat": {"available": True, "label": "chat"},
                "vision": {"available": False, "label": "未配置"},
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
        plugin.tts_timeout_seconds = 60
        plugin.tts_volume_ratio = 0.65
        plugin.realtime_duplex_enabled = True
        plugin.watch_auto_comment = True
        plugin.watch_comment_interval_seconds = 30
        plugin.watch_scene_min_interval_seconds = 8
        plugin.watch_duck_video_volume = True
        plugin.watch_duck_volume_ratio = 0.3
        room = RoomSession("room", "ticket", "watch", "123", None)

        bootstrap = await plugin.room_bootstrap(room)

        self.assertEqual("zh-CN", bootstrap["stt"]["browser_language"])
        self.assertEqual("ja-JP", bootstrap["tts"]["browser_language"])
        self.assertEqual(60, bootstrap["tts"]["timeout_seconds"])
        self.assertEqual(0.65, bootstrap["tts"]["volume_ratio"])
        self.assertTrue(bootstrap["call"]["camera_available"])
        self.assertFalse(bootstrap["call"]["camera_vision_available"])
        self.assertTrue(bootstrap["call"]["realtime_duplex_enabled"])

    def test_browser_fallback_uses_tts_language(self) -> None:
        source = (
            Path(__file__).resolve().parents[1] / "web" / "app.js"
        ).read_text(encoding="utf-8")

        self.assertRegex(
            source,
            re.compile(
                r"speakInBrowser\(\s*message\.text,\s*message\.language,\s*"
                r"message\.display_text,\s*message\.source,\s*"
                r"message\.after_playback_action,\s*\)",
                re.DOTALL,
            ),
        )
        self.assertIn(
            'utterance.lang = language || state.room?.tts?.browser_language || "zh-CN";',
            source,
        )
        self.assertNotIn(
            'utterance.lang = state.room?.stt?.browser_language || "zh-CN";',
            source,
        )

    async def test_text_only_fallback_never_arms_hangup(self) -> None:
        provider = SimpleNamespace(get_audio=AsyncMock())
        api = SimpleNamespace(
            synthesize_realtime_voice=AsyncMock(
                return_value={"audio_path": "", "fallback_text": "", "language": "ja-JP"}
            )
        )
        plugin = self._plugin(provider, api)
        room = RoomSession("room", "ticket", "call", "123", None)
        room.call_active = True

        queued = await plugin._synthesize_and_send(
            room,
            "晚安。",
            display_text="晚安。",
            after_playback_action="hangup",
        )

        payload = plugin.send_room_payload.await_args_list[-1].args[1]
        self.assertFalse(queued)
        self.assertEqual("bot_text", payload["type"])
        self.assertNotIn("after_playback_action", payload)

    def test_frontend_runs_hangup_only_after_successful_playback(self) -> None:
        source = (
            Path(__file__).resolve().parents[1] / "web" / "app.js"
        ).read_text(encoding="utf-8")
        bot_text_handler = source[source.index('case "bot_text":') : source.index('case "audio":')]
        fallback_without_voice = source[
            source.index("else {", source.index('case "tts_fallback":')) : source.index('case "stop_audio":')
        ]

        self.assertNotIn("runAfterPlaybackAction", bot_text_handler)
        self.assertNotIn("runAfterPlaybackAction", fallback_without_voice)
        self.assertIn(
            "playbackCompleted && runAfterPlaybackAction(item?.after_playback_action)",
            source,
        )
        self.assertIn(
            "playbackCompleted && runAfterPlaybackAction(afterPlaybackAction)",
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
