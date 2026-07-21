# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from contextlib import suppress
from types import SimpleNamespace
import unittest

from astrbot_stubs import install_astrbot_stubs

install_astrbot_stubs()

from astrbot_plugin_together_companion.main import TogetherCompanionPlugin
from astrbot_plugin_together_companion.models import RoomSession


class RoomTaskLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_room_waits_for_cancelled_generation(self) -> None:
        plugin = TogetherCompanionPlugin.__new__(TogetherCompanionPlugin)
        room = RoomSession("room", "ticket", "call", "123", None)
        cancelled = asyncio.Event()
        revoked: list[str] = []

        async def generation():
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async def no_op(_room):
            return None

        task = asyncio.create_task(generation())
        await asyncio.sleep(0)
        room.generation_task = task
        plugin.rooms = {room.room_id: room}
        plugin.detached_rooms = {}
        plugin.media_sources = {}
        plugin.record_shared_experiences = False
        plugin.ticket_store = SimpleNamespace(revoke=lambda token: revoked.append(token))
        plugin._stop_live_mouth_sync = no_op
        plugin._notify_shared_activity_ended = no_op

        await plugin.close_room(room)

        self.assertTrue(cancelled.is_set())
        self.assertTrue(task.done())
        self.assertNotIn(room.room_id, plugin.rooms)
        self.assertEqual([room.ticket_token], revoked)

    async def test_media_resolution_does_not_replace_chat_generation(self) -> None:
        plugin = TogetherCompanionPlugin.__new__(TogetherCompanionPlugin)
        room = RoomSession("room", "ticket", "watch", "123", None)
        release_chat = asyncio.Event()
        release_media = asyncio.Event()

        async def chat_operation():
            await release_chat.wait()

        async def media_operation():
            await release_media.wait()

        plugin._start_room_task(room, chat_operation())
        chat_task = room.generation_task
        plugin._start_media_resolution(room, media_operation())
        media_task = room.media_resolution_task

        self.assertIsNotNone(chat_task)
        self.assertIsNotNone(media_task)
        self.assertIsNot(chat_task, media_task)
        self.assertFalse(chat_task.cancelled())

        room.cancel_generation()
        room.cancel_media_resolution()
        with suppress(asyncio.CancelledError):
            await chat_task
        with suppress(asyncio.CancelledError):
            await media_task

    async def test_mode_change_cancels_generation_and_stops_audio(self) -> None:
        plugin = TogetherCompanionPlugin.__new__(TogetherCompanionPlugin)
        room = RoomSession("room", "ticket", "watch", "123", None)
        release = asyncio.Event()
        sent: list[dict] = []

        async def generation():
            await release.wait()

        async def send(_room, payload):
            sent.append(payload)

        plugin.send_room_payload = send
        plugin._start_room_task(room, generation())
        task = room.generation_task

        await plugin.handle_room_payload(room, {"type": "set_mode", "mode": "call"})

        self.assertEqual("call", room.mode)
        self.assertTrue(task.cancelling())
        self.assertEqual("mode", sent[0]["type"])
        self.assertEqual("stop_audio", sent[1]["type"])
        with suppress(asyncio.CancelledError):
            await task

    async def test_forced_final_memory_replaces_incomplete_refresh(self) -> None:
        class Provider:
            async def text_chat(self, *, prompt, system_prompt):
                self.prompt = prompt
                return SimpleNamespace(completion_text="共同反应：结尾时 Bot 觉得这个收束很自然。")

        plugin = TogetherCompanionPlugin.__new__(TogetherCompanionPlugin)
        provider = Provider()
        plugin._get_chat_provider = lambda: provider
        plugin.watch_memory_refresh_seconds = 240
        room = RoomSession("room", "ticket", "watch", "123", None)
        room.media_state = {"current_time": 120.0}
        room.append_watch_event("ended", "影片播放结束", media_time=120)
        room.append_watch_event("bot", "Bot 说：这个收束很自然。", media_time=120)
        plugin.rooms = {room.room_id: room}

        blocker = asyncio.create_task(asyncio.Event().wait())
        room.watch_memory_task = blocker
        room.watch_memory_refreshing = True

        plugin._schedule_watch_memory_refresh(room, force=True)
        replacement = room.watch_memory_task
        self.assertIsNot(replacement, blocker)
        with suppress(asyncio.CancelledError):
            await blocker
        await replacement

        self.assertIn("共同反应", room.watch_memory)
        self.assertIn("[Bot 反应]", provider.prompt)


class RoomResumeLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def _plugin(self, grace: float) -> tuple:
        plugin = TogetherCompanionPlugin.__new__(TogetherCompanionPlugin)
        revoked: list[str] = []

        async def no_op(*_args, **_kwargs):
            return None

        plugin.rooms = {}
        plugin.detached_rooms = {}
        plugin.room_resume_grace_seconds = grace
        plugin.media_sources = {}
        plugin.record_shared_experiences = False
        plugin.ticket_store = SimpleNamespace(revoke=lambda token: revoked.append(token))
        plugin._stop_live_mouth_sync = no_op
        plugin._notify_shared_activity_ended = no_op
        return plugin, revoked

    def _room(self, plugin) -> RoomSession:
        room = RoomSession("room", "ticket", "call", "123", None)
        room.resume_token = "resume-token"
        plugin.rooms[room.room_id] = room
        return room

    async def test_detach_keeps_session_and_closes_after_grace(self) -> None:
        plugin, revoked = self._plugin(grace=0.05)
        room = self._room(plugin)
        websocket = object()
        room.websocket = websocket

        await plugin.detach_room(room)

        self.assertIsNone(room.websocket)
        self.assertIs(plugin.detached_rooms.get("resume-token"), room)
        self.assertIn(room.room_id, plugin.rooms)
        self.assertFalse(room.integration_closed)

        await asyncio.sleep(0.15)

        self.assertTrue(room.integration_closed)
        self.assertNotIn("resume-token", plugin.detached_rooms)
        self.assertNotIn(room.room_id, plugin.rooms)
        self.assertEqual([room.ticket_token], revoked)

    async def test_resume_reattaches_and_cancels_grace_close(self) -> None:
        plugin, _revoked = self._plugin(grace=3600)
        room = self._room(plugin)
        await plugin.detach_room(room)
        grace_task = room.detach_close_task
        self.assertIsNotNone(grace_task)

        websocket = object()
        resumed = await plugin.resume_room("resume-token", websocket)

        self.assertIs(resumed, room)
        self.assertIs(room.websocket, websocket)
        self.assertNotIn("resume-token", plugin.detached_rooms)
        self.assertIsNone(room.detach_close_task)
        with suppress(asyncio.CancelledError):
            await grace_task
        self.assertTrue(grace_task.cancelled())
        self.assertFalse(room.integration_closed)

        await plugin.close_room(room)
        self.assertTrue(room.integration_closed)

    async def test_close_room_clears_detached_state(self) -> None:
        plugin, _revoked = self._plugin(grace=3600)
        room = self._room(plugin)
        await plugin.detach_room(room)
        grace_task = room.detach_close_task

        await plugin.close_room(room)

        self.assertNotIn("resume-token", plugin.detached_rooms)
        with suppress(asyncio.CancelledError):
            await grace_task
        self.assertTrue(grace_task.cancelled())
        self.assertTrue(room.integration_closed)


if __name__ == "__main__":
    unittest.main()
