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
