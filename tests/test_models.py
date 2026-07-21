# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib.util
import sys
import time
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "models.py"
SPEC = importlib.util.spec_from_file_location("together_companion_models", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODELS = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODELS
SPEC.loader.exec_module(MODELS)


class RoomTicketStoreTests(unittest.TestCase):
    def test_mode_is_normalized_when_ticket_is_issued(self) -> None:
        store = MODELS.RoomTicketStore(ttl_seconds=300)
        ticket = store.issue(mode="unexpected", user_id=" 12345 ")
        self.assertEqual("call", ticket.mode)
        self.assertEqual("12345", ticket.user_id)
        self.assertIs(store.get(ticket.token), ticket)

    def test_expired_ticket_is_pruned(self) -> None:
        store = MODELS.RoomTicketStore(ttl_seconds=60)
        ticket = store.issue(mode="watch")
        ticket.expires_at = time.time() - 1
        self.assertIsNone(store.get(ticket.token))

    def test_ticket_can_only_be_consumed_once(self) -> None:
        store = MODELS.RoomTicketStore(ttl_seconds=60)
        ticket = store.issue(mode="watch", user_id="123")

        self.assertIs(ticket, store.consume(ticket.token))
        self.assertIsNone(store.consume(ticket.token))
        self.assertIsNone(store.get(ticket.token))


class RoomSessionTests(unittest.TestCase):
    def test_history_keeps_complete_recent_turns(self) -> None:
        room = MODELS.RoomSession(
            room_id="room",
            ticket_token="ticket",
            mode="call",
            user_id="123",
            websocket=None,
        )
        for index in range(8):
            room.append_turn("user", f"u{index}", history_turns=2)
            room.append_turn("assistant", f"a{index}", history_turns=2)
        self.assertEqual(4, len(room.history))
        self.assertEqual("u6", room.history[0]["content"])
        self.assertEqual("a7", room.history[-1]["content"])

    def test_empty_turn_is_ignored(self) -> None:
        room = MODELS.RoomSession("room", "ticket", "call", "", None)
        room.append_turn("user", "   ", history_turns=3)
        self.assertEqual([], room.history)

    def test_watch_events_are_ordered_and_reset_with_media(self) -> None:
        room = MODELS.RoomSession("room", "ticket", "watch", "123", None)
        first = room.append_watch_event("media", "开始观看", media_time=0)
        second = room.append_watch_event("user", "用户说：这里好安静", media_time=12.5)
        self.assertEqual(1, first["seq"])
        self.assertEqual(2, second["seq"])
        room.watch_knowledge = "无剧透背景"
        room.watch_memory = "已经看过开场"

        room.reset_watch(media_token="next-token")

        self.assertEqual("next-token", room.media_token)
        self.assertEqual([], room.watch_events)
        self.assertEqual("", room.watch_knowledge)
        self.assertEqual("", room.watch_memory)
        self.assertEqual(1, room.watch_epoch)


if __name__ == "__main__":
    unittest.main()
