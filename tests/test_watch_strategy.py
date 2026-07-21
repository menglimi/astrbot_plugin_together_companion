# -*- coding: utf-8 -*-
from __future__ import annotations

from types import SimpleNamespace
import unittest

from astrbot_stubs import install_astrbot_stubs

install_astrbot_stubs()

from astrbot_plugin_together_companion.main import TogetherCompanionPlugin
from astrbot_plugin_together_companion.media import ResolvedMedia
from astrbot_plugin_together_companion.models import RoomSession


class WatchStrategyTests(unittest.TestCase):
    def test_internal_json_is_parsed_without_exposing_fields(self) -> None:
        decision = TogetherCompanionPlugin._parse_watch_decision(
            '{"speak":true,"utterance":"这个转折还挺突然的。","observation":"画面中的人物突然停下。","expires_in":9}',
            trigger="scene_change",
        )
        self.assertTrue(decision["speak"])
        self.assertEqual("这个转折还挺突然的。", decision["utterance"])
        self.assertEqual("画面中的人物突然停下。", decision["observation"])

    def test_malformed_internal_payload_is_not_sent_as_dialogue(self) -> None:
        decision = TogetherCompanionPlugin._parse_watch_decision(
            '{"speak": true, "utterance":',
            trigger="scene_change",
        )
        self.assertFalse(decision["speak"])
        self.assertEqual("", decision["utterance"])

    def test_automatic_comment_expires_after_scene_has_passed(self) -> None:
        room = RoomSession("room", "ticket", "watch", "123", None)
        room.media_state = {"current_time": 42.0, "paused": False}
        self.assertTrue(
            TogetherCompanionPlugin._watch_comment_is_stale(
                room,
                epoch=0,
                captured_at=20.0,
                expires_in=10.0,
                trigger="scene_change",
            )
        )

    def test_watch_context_keeps_knowledge_and_played_facts_separate(self) -> None:
        plugin = TogetherCompanionPlugin.__new__(TogetherCompanionPlugin)
        plugin.media_sources = {}
        room = RoomSession("room", "ticket", "watch", "123", None)
        room.watch_knowledge = "来源：B站视频页公开信息（标题）\n背景：这是一部动画。"
        room.watch_memory = "已确认剧情：主角刚刚到达车站。"
        room.append_watch_event("observation", "镜头停在站台", media_time=18)

        context = plugin._format_watch_context(room)

        self.assertIn("观前无剧透背景", context)
        self.assertIn("观中剧情笔记", context)
        self.assertIn("[画面观察]", context)

    def test_watch_context_excludes_conversation_and_subtitle_events(self) -> None:
        plugin = TogetherCompanionPlugin.__new__(TogetherCompanionPlugin)
        plugin.media_sources = {}
        room = RoomSession("room", "ticket", "watch", "123", None)
        room.append_watch_event("user", "用户说：这里好安静", media_time=12)
        room.append_watch_event("bot", "Bot 说：是啊", media_time=13)
        room.append_watch_event("subtitle", "列车即将进站", media_time=14)
        room.append_watch_event("observation", "镜头停在站台", media_time=15)

        context = plugin._format_watch_context(room)

        self.assertNotIn("用户说：这里好安静", context)
        self.assertNotIn("Bot 说：是啊", context)
        self.assertNotIn("列车即将进站", context)
        self.assertIn("镜头停在站台", context)

    def test_public_metadata_material_is_source_labeled(self) -> None:
        source = ResolvedMedia(
            token="token",
            room_id="room",
            source_url="https://example.com/video.mp4",
            title="测试影片",
            uploader="测试作者",
            category="动画",
            description="一段不涉及剧情的公开介绍。",
            tags=["幻想", "冒险"],
        )

        material, fields = TogetherCompanionPlugin._watch_knowledge_material(source)

        self.assertIn("UP 主：测试作者", material)
        self.assertIn("标签：幻想、冒险", material)
        self.assertEqual("标题、UP 主、分区、标签、公开简介", fields)


class WatchDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_ending_frames_only_start_one_comment(self) -> None:
        plugin = TogetherCompanionPlugin.__new__(TogetherCompanionPlugin)
        plugin.watch_auto_comment = True
        room = RoomSession("room", "ticket", "watch", "123", None)
        scheduled = []

        async def generate(*_args, **_kwargs):
            return None

        def start(_room, operation, **_kwargs):
            scheduled.append(operation)
            operation.close()

        plugin._generate_watch_comment = generate
        plugin._start_room_task = start
        payload = {
            "type": "watch_frame",
            "trigger": "ending",
            "image": "data:image/png;base64,iVBORw0KGgo=",
            "captured_at": 120,
        }

        await plugin.handle_room_payload(room, payload)
        await plugin.handle_room_payload(room, payload)

        self.assertEqual(1, len(scheduled))
        self.assertEqual(room.watch_epoch, room.watch_ending_epoch)

        room.reset_watch()
        await plugin.handle_room_payload(room, payload)
        self.assertEqual(2, len(scheduled))

    async def test_prepared_knowledge_records_public_source_scope(self) -> None:
        class Provider:
            async def text_chat(self, *, prompt, system_prompt):
                self.prompt = prompt
                self.system_prompt = system_prompt
                return SimpleNamespace(completion_text="背景：这是一部幻想题材动画。")

        plugin = TogetherCompanionPlugin.__new__(TogetherCompanionPlugin)
        provider = Provider()
        plugin._get_chat_provider = lambda: provider
        room = RoomSession("room", "ticket", "watch", "123", None)
        source = ResolvedMedia(
            token="media-token",
            room_id=room.room_id,
            source_url="https://example.com/video.mp4",
            page_url="https://www.bilibili.com/video/BV1PgKr6uExx/",
            title="测试影片",
            category="动画",
        )
        room.reset_watch(media_token=source.token)
        plugin.rooms = {room.room_id: room}
        material, _fields = plugin._watch_knowledge_material(source)

        await plugin._prepare_watch_knowledge(room, source, material, epoch=room.watch_epoch)

        self.assertIn("来源：B站视频页公开信息（标题、分区）", room.watch_knowledge)
        self.assertIn(source.page_url, room.watch_knowledge)
        self.assertIn("背景：这是一部幻想题材动画。", room.watch_knowledge)
        self.assertNotIn("字幕", provider.prompt)

    async def test_only_parsed_utterance_is_delivered(self) -> None:
        plugin = TogetherCompanionPlugin.__new__(TogetherCompanionPlugin)
        room = RoomSession("room", "ticket", "watch", "123", None)
        room.media_state = {"current_time": 8.0, "duration": 120.0, "paused": False, "title": "测试影片"}
        plugin.rooms = {room.room_id: room}
        plugin.media_sources = {}
        plugin.history_turns = 6
        plugin.watch_memory_refresh_seconds = 240
        sent = []
        spoken = []

        async def generate(_room, _prompt, **_kwargs):
            return '{"speak":true,"utterance":"这个转折还挺突然的。","observation":"画面中的人物突然停下。","expires_in":12}'

        async def send(_room, payload):
            sent.append(payload)

        async def synthesize(_room, text, **kwargs):
            spoken.append((text, kwargs))

        plugin._generate_model_text = generate
        plugin.send_room_payload = send
        plugin._synthesize_and_send = synthesize

        await plugin._generate_watch_comment(
            room,
            "data:image/jpeg;base64,ignored",
            trigger="opening",
            captured_at=8.0,
        )

        visible = [item.get("text") for item in sent if item.get("type") == "bot_text"]
        self.assertEqual([], visible)
        self.assertEqual("这个转折还挺突然的。", spoken[0][0])
        self.assertEqual("这个转折还挺突然的。", spoken[0][1]["display_text"])
        self.assertEqual("watch_comment", spoken[0][1]["display_source"])
        self.assertNotIn('"speak"', spoken[0][0])


if __name__ == "__main__":
    unittest.main()
