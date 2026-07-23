# -*- coding: utf-8 -*-
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class _VideoParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.video_attributes: dict[str, str | None] = {}
        self.ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "video" and attributes.get("id") == "watchVideo":
            self.video_attributes = attributes
        element_id = attributes.get("id")
        if element_id:
            self.ids.add(element_id)


class WebPlayerTests(unittest.TestCase):
    def test_call_view_contains_camera_controls_and_inline_preview(self) -> None:
        parser = _VideoParser()
        parser.feed((ROOT / "web" / "index.html").read_text(encoding="utf-8"))
        source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

        self.assertTrue({"callCamera", "cameraToggle", "switchCamera", "cameraStage", "cameraDeviceSelect"}.issubset(parser.ids))
        self.assertIn("captureCameraFrameData", source)
        self.assertIn('type: "call_frame"', source)
        self.assertIn("window.setInterval(sendCameraFrame, 8000)", source)

    def test_call_view_uses_full_bleed_scene_and_overlay_controls(self) -> None:
        page = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        styles = (ROOT / "web" / "app.css").read_text(encoding="utf-8")
        source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

        self.assertIn('class="call-identity"', page)
        self.assertIn("position: fixed;\n  inset: 0;", styles)
        self.assertIn(".call-view > .transcript {", styles)
        self.assertIn('body[data-mode="call"] .message-composer', styles)
        self.assertIn("@keyframes overlayRiseIn", styles)
        self.assertIn('document.body.classList.add("call-camera-on")', source)
        self.assertIn('document.body.classList.remove("call-camera-on")', source)

    def test_watch_transcript_scrolls_without_moving_room_page(self) -> None:
        parser = _VideoParser()
        parser.feed((ROOT / "web" / "index.html").read_text(encoding="utf-8"))
        page = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        styles = (ROOT / "web" / "app.css").read_text(encoding="utf-8")

        self.assertIn('id="watchTranscript" tabindex="0"', page)
        self.assertIn("overscroll-behavior-y: contain", styles)
        self.assertIn("scrollbar-gutter: stable", styles)
        self.assertIn(
            'body[data-mode="watch"], body[data-mode="work"] { height: 100dvh; overflow-y: hidden; }',
            styles,
        )
        self.assertIn("height: calc(100dvh - 164px)", styles)

    def test_watch_view_has_independent_tts_toggle(self) -> None:
        page = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="watchTtsEnabled" type="checkbox"', page)
        self.assertIn("语音回复", page)
        self.assertIn('send({ type: "set_watch_tts", enabled })', source)
        self.assertIn("后续观影回复仅显示文字", source)

    def test_tts_volume_controls_server_audio_and_browser_fallback(self) -> None:
        parser = _VideoParser()
        parser.feed((ROOT / "web" / "index.html").read_text(encoding="utf-8"))
        source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "web" / "app.css").read_text(encoding="utf-8")

        self.assertTrue({"ttsVolume", "ttsVolumeValue"}.issubset(parser.ids))
        self.assertIn('const TTS_VOLUME_STORAGE_KEY = "together_tts_volume_percent"', source)
        self.assertIn("Number(state.room?.tts?.volume_ratio) * 100", source)
        self.assertIn("audio.volume = state.ttsVolume", source)
        self.assertIn("utterance.volume = state.ttsVolume", source)
        self.assertIn("state.currentAudio.volume = state.ttsVolume", source)
        self.assertIn("state.browserUtterance.volume = state.ttsVolume", source)
        self.assertIn("max-height: calc(100dvh - 108px)", styles)
        self.assertIn("overscroll-behavior-y: contain", styles)

    def test_camera_defaults_front_and_releases_current_track_before_switching(self) -> None:
        source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

        self.assertIn('facingMode: { exact: "user" }', source)
        release_index = source.index("if (releaseCurrent && previous)")
        acquire_index = source.index("const nextStream = await navigator.mediaDevices.getUserMedia", release_index)
        self.assertLess(release_index, acquire_index)
        self.assertIn("切换失败，已恢复原摄像头", source)

    def test_camera_detection_is_independent_from_vision_provider(self) -> None:
        source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

        self.assertIn("function cameraVisionAvailable()", source)
        self.assertNotIn('if (!state.room?.call?.camera_available) { showToast("当前没有可用的视觉模型")', source)
        self.assertIn("const detectedDevices = (await navigator.mediaDevices.enumerateDevices())", source)
        self.assertIn("const devices = detectedDevices.filter((item) => item.deviceId)", source)
        self.assertIn("首次开启授权后会显示设备名称", source)
        self.assertIn("await replaceCameraStream(true, { mirror: true })", source)
        self.assertIn("|| !cameraVisionAvailable()", source)

    def test_camera_can_be_selected_and_remembered(self) -> None:
        source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

        self.assertIn('localStorage.setItem("together_camera_device"', source)
        self.assertIn("async function refreshCameraDevices", source)
        self.assertIn("async function selectCameraDevice", source)
        self.assertIn("{ deviceId: { exact: preferred.deviceId } }", source)
        self.assertIn('navigator.mediaDevices?.addEventListener?.("devicechange"', source)
        self.assertNotIn('cameraLooksRear(next.label) || currentSettings.facingMode === "user"', source)

    def test_camera_uses_hd_preview_but_compressed_model_frames(self) -> None:
        source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

        self.assertIn("const CAMERA_PREVIEW_WIDTH = 1920", source)
        self.assertIn("const CAMERA_PREVIEW_HEIGHT = 1080", source)
        self.assertIn("const CAMERA_PREVIEW_FRAME_RATE = 30", source)
        self.assertIn("const CAMERA_UPLOAD_MAX_WIDTH = 640", source)
        self.assertIn("const CAMERA_UPLOAD_JPEG_QUALITY = .70", source)
        self.assertIn("width: { ideal: CAMERA_PREVIEW_WIDTH }", source)
        self.assertIn("height: { ideal: CAMERA_PREVIEW_HEIGHT }", source)
        self.assertIn("CAMERA_UPLOAD_MAX_WIDTH,", source)
        self.assertIn("CAMERA_UPLOAD_JPEG_QUALITY,", source)
        self.assertIn("${currentWidth}×${currentHeight}", source)

    def test_connected_room_can_request_and_copy_fresh_invite_link(self) -> None:
        parser = _VideoParser()
        parser.feed((ROOT / "web" / "index.html").read_text(encoding="utf-8"))
        source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

        self.assertTrue({"inviteButton", "inviteDialog", "inviteUrl", "copyInviteLink"}.issubset(parser.ids))
        self.assertIn('send({ type: "create_invite" })', source)
        self.assertIn('case "invite_link"', source)

    def test_room_accepts_join_path_and_legacy_ticket_query(self) -> None:
        source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

        self.assertIn("window.location.pathname.match", source)
        self.assertIn('params.get("ticket")', source)
        self.assertIn('const fromUrl = fromPath || fromQuery', source)

    def test_browser_stt_permission_failure_falls_back_without_hanging_up(self) -> None:
        source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

        self.assertIn("fallbackFromBrowserRecognition(event.error)", source)
        self.assertIn('fallbackFromBrowserRecognition("not-allowed")', source)
        self.assertIn("浏览器语音识别不可用，已切换到 AstrBot 按键讲话", source)
        self.assertIn("浏览器语音识别不可用，已切换到 AstrBot 自由讲话", source)
        self.assertIn("仍可使用文字和摄像头通话", source)
        self.assertNotIn("requestMicrophonePermission", source)
        self.assertNotIn('throw new Error("当前环境不支持麦克风访问")', source)
        self.assertNotIn('["not-allowed", "service-not-allowed"].includes(event.error)) stopCall(false)', source)

    def test_private_tts_markup_is_never_rendered_as_bot_text(self) -> None:
        source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

        self.assertIn("function sanitizeBotDisplayText(value)", source)
        self.assertIn("const visibleBotText = sanitizeBotDisplayText(message.text)", source)
        self.assertIn("sanitizeBotDisplayText(message.display_text || message.text)", source)

    def test_free_and_push_to_talk_modes_share_customizable_key_controls(self) -> None:
        parser = _VideoParser()
        parser.feed((ROOT / "web" / "index.html").read_text(encoding="utf-8"))
        source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

        self.assertTrue({"talkKeySetting", "talkKeyCapture", "talkKeyLabel", "holdToTalk"}.issubset(parser.ids))
        self.assertIn('localStorage.setItem("together_talk_mode"', source)
        self.assertIn('localStorage.setItem("together_talk_key"', source)
        self.assertIn("function beginPushToTalk", source)
        self.assertIn("function endPushToTalk", source)
        self.assertIn("async function startVoiceActivityDetection", source)
        self.assertIn('state.talkMode === "push" && !state.pushToTalkHeld', source)

    def test_recognized_speech_is_immediate_but_can_be_excluded(self) -> None:
        source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "web" / "app.css").read_text(encoding="utf-8")

        self.assertIn('sendUserText(finalText.trim(), "browser_stt", alternatives)', source)
        self.assertIn('type: "exclude_utterance"', source)
        self.assertIn('data-lucide="x"', source)
        self.assertIn(".utterance-cancel", styles)
        self.assertNotIn("auto_confirm_ms", source)

    def test_browser_sends_local_time_with_timezone_for_call_context(self) -> None:
        source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

        self.assertIn("function clientTimeContext()", source)
        self.assertIn("client_local_time: clientLocalTime", source)
        self.assertIn("client_timezone: clientTimezone", source)
        self.assertGreaterEqual(source.count("...clientTimeContext()"), 2)
        self.assertIn('send({ type: "call_idle", ...clientTimeContext() })', source)
        self.assertIn(
            "state.room?.call?.proactive_enabled || state.room?.call?.model_hangup_enabled",
            source,
        )

    def test_custom_controls_replace_native_video_controls(self) -> None:
        parser = _VideoParser()
        parser.feed((ROOT / "web" / "index.html").read_text(encoding="utf-8"))

        self.assertNotIn("controls", parser.video_attributes)
        self.assertIn("watchAudio", parser.ids)
        self.assertTrue(
            {
                "videoControls",
                "videoPlayPause",
                "videoSeekBack",
                "videoSeekForward",
                "videoProgress",
                "videoMute",
                "videoVolume",
                "videoRate",
            }.issubset(parser.ids)
        )

    def test_long_press_temporarily_uses_two_times_speed(self) -> None:
        source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

        self.assertIn("state.videoRateBeforeHold = video.playbackRate || 1", source)
        self.assertIn("video.playbackRate = 2", source)
        self.assertIn("video.playbackRate = state.videoRateBeforeHold || 1", source)
        self.assertIn("window.setTimeout(() => activateTemporaryVideoRate(source), 360)", source)

    def test_playing_controls_hide_after_pointer_inactivity(self) -> None:
        source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "web" / "app.css").read_text(encoding="utf-8")

        self.assertIn("window.setTimeout(hideVideoControls, 2200)", source)
        self.assertIn('videoStage.addEventListener("pointermove", () => showVideoControls())', source)
        self.assertIn(".video-stage.is-playing.controls-hidden .video-controls", styles)

    def test_dash_audio_is_synchronized_with_video(self) -> None:
        source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

        self.assertIn('message.playback_mode === "dash" ? message.audio_url : ""', source)
        self.assertIn("function syncDashAudio", source)
        self.assertIn("audio.playbackRate = video.playbackRate || 1", source)


if __name__ == "__main__":
    unittest.main()
