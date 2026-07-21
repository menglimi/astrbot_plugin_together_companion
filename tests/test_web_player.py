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

        self.assertTrue({"callCamera", "cameraToggle", "switchCamera", "cameraStage"}.issubset(parser.ids))
        self.assertIn("captureCameraFrameData", source)
        self.assertIn('type: "call_frame"', source)
        self.assertIn("window.setInterval(sendCameraFrame, 8000)", source)

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
        self.assertIn("浏览器语音识别不可用，已切换到按住说话", source)
        self.assertIn("仍可使用文字和摄像头通话", source)
        self.assertNotIn('["not-allowed", "service-not-allowed"].includes(event.error)) stopCall(false)', source)

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

    def test_long_press_temporarily_uses_three_times_speed(self) -> None:
        source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

        self.assertIn("state.videoRateBeforeHold = video.playbackRate || 1", source)
        self.assertIn("video.playbackRate = 3", source)
        self.assertIn("video.playbackRate = state.videoRateBeforeHold || 1", source)
        self.assertIn("window.setTimeout(() => activateTemporaryVideoRate(source), 360)", source)

    def test_dash_audio_is_synchronized_with_video(self) -> None:
        source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

        self.assertIn('message.playback_mode === "dash" ? message.audio_url : ""', source)
        self.assertIn("function syncDashAudio", source)
        self.assertIn("audio.playbackRate = video.playbackRate || 1", source)


if __name__ == "__main__":
    unittest.main()
