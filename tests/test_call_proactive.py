# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import time
import unittest

from astrbot_stubs import install_astrbot_stubs

install_astrbot_stubs()

from astrbot_plugin_together_companion.main import TogetherCompanionPlugin
from astrbot_plugin_together_companion.models import RoomSession


class CallProactiveTests(unittest.IsolatedAsyncioTestCase):
    async def test_idle_event_only_starts_after_configured_wait(self) -> None:
        plugin = TogetherCompanionPlugin.__new__(TogetherCompanionPlugin)
        plugin.call_proactive_enabled = True
        plugin.call_idle_seconds = 120
        generated = []

        async def generate(_room, *, idle_seconds):
            generated.append(idle_seconds)

        plugin._generate_call_proactive = generate
        room = RoomSession("room", "ticket", "call", "995051631", None)
        room.call_active = True
        room.call_last_user_activity = time.monotonic()

        await plugin.handle_room_payload(room, {"type": "call_idle"})
        self.assertEqual([], generated)
        self.assertIsNone(room.generation_task)

        room.call_last_user_activity = time.monotonic() - 125
        await plugin.handle_room_payload(room, {"type": "call_idle"})
        await room.generation_task

        self.assertEqual(1, len(generated))
        self.assertGreaterEqual(generated[0], 120)

    async def test_user_activity_prevents_stale_idle_trigger(self) -> None:
        plugin = TogetherCompanionPlugin.__new__(TogetherCompanionPlugin)
        plugin.call_proactive_enabled = True
        plugin.call_idle_seconds = 60
        room = RoomSession("room", "ticket", "call", "995051631", None)

        await plugin.handle_room_payload(room, {"type": "call_state", "active": True})
        before = room.call_last_user_activity
        await asyncio.sleep(0)
        await plugin.handle_room_payload(room, {"type": "call_activity"})

        self.assertTrue(room.call_active)
        self.assertGreaterEqual(room.call_last_user_activity, before)

    async def test_model_can_choose_to_remain_silent(self) -> None:
        plugin = TogetherCompanionPlugin.__new__(TogetherCompanionPlugin)
        plugin.history_turns = 6
        room = RoomSession("room", "ticket", "call", "995051631", None)
        room.call_active = True
        spoken = []

        async def generate(*_args, **_kwargs):
            return '{"speak":false,"utterance":""}'

        plugin._generate_model_text = generate
        plugin._push_live_subtitle = lambda *_args, **_kwargs: None
        plugin._synthesize_and_send = lambda *_args, **_kwargs: spoken.append(_args)

        await plugin._generate_call_proactive(room, idle_seconds=120)

        self.assertEqual([], spoken)
        self.assertEqual([], room.history)

    async def test_spoken_topic_is_delivered_as_call_proactive(self) -> None:
        plugin = TogetherCompanionPlugin.__new__(TogetherCompanionPlugin)
        plugin.history_turns = 6
        room = RoomSession("room", "ticket", "call", "995051631", None)
        room.call_active = True
        delivered = []
        statuses = []

        async def generate(*_args, **kwargs):
            self.assertTrue(kwargs["call_proactive"])
            return '{"speak":true,"utterance":"突然想到，我们上次说的那部电影后来还看吗？"}'

        async def push(*_args, **_kwargs):
            return None

        async def synthesize(_room, text, **kwargs):
            delivered.append((text, kwargs))

        async def send(_room, payload):
            statuses.append(payload)

        plugin._generate_model_text = generate
        plugin._push_live_subtitle = push
        plugin._synthesize_and_send = synthesize
        plugin.send_room_payload = send

        await plugin._generate_call_proactive(room, idle_seconds=120)

        self.assertEqual("assistant", room.history[-1]["role"])
        self.assertEqual("call_proactive", delivered[0][1]["display_source"])
        self.assertEqual("listening", statuses[-1]["state"])


if __name__ == "__main__":
    unittest.main()
