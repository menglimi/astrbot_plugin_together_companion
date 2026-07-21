# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from typing import Any


VALID_ROOM_MODES = {"call", "watch"}


def normalize_room_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in VALID_ROOM_MODES else "call"


@dataclass(slots=True)
class RoomTicket:
    token: str
    mode: str
    user_id: str
    created_at: float
    expires_at: float

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at


class RoomTicketStore:
    def __init__(self, ttl_seconds: int = 600) -> None:
        self.ttl_seconds = max(60, min(int(ttl_seconds or 600), 86400))
        self._tickets: dict[str, RoomTicket] = {}

    def issue(self, *, mode: str, user_id: str = "") -> RoomTicket:
        self.prune()
        now = time.time()
        ticket = RoomTicket(
            token=secrets.token_urlsafe(32),
            mode=normalize_room_mode(mode),
            user_id=str(user_id or "").strip()[:80],
            created_at=now,
            expires_at=now + self.ttl_seconds,
        )
        self._tickets[ticket.token] = ticket
        return ticket

    def get(self, token: str) -> RoomTicket | None:
        self.prune()
        ticket = self._tickets.get(str(token or ""))
        if ticket is None or ticket.expired:
            return None
        return ticket

    def consume(self, token: str) -> RoomTicket | None:
        """Return and revoke a ticket in one event-loop-safe operation."""
        self.prune()
        ticket = self._tickets.pop(str(token or ""), None)
        if ticket is None or ticket.expired:
            return None
        return ticket

    def revoke(self, token: str) -> None:
        self._tickets.pop(str(token or ""), None)

    def prune(self) -> int:
        expired = [token for token, ticket in self._tickets.items() if ticket.expired]
        for token in expired:
            self._tickets.pop(token, None)
        return len(expired)


@dataclass
class RoomSession:
    room_id: str
    ticket_token: str
    mode: str
    user_id: str
    websocket: Any
    created_at: float = field(default_factory=time.time)
    history: list[dict[str, str]] = field(default_factory=list)
    media_state: dict[str, Any] = field(default_factory=dict)
    media_token: str = ""
    media_resolution_task: asyncio.Task | None = None
    watch_events: list[dict[str, Any]] = field(default_factory=list)
    watch_event_sequence: int = 0
    watch_knowledge: str = ""
    watch_knowledge_task: asyncio.Task | None = None
    watch_memory: str = ""
    watch_memory_cursor: int = 0
    watch_memory_media_time: float = 0.0
    watch_memory_refreshing: bool = False
    watch_memory_task: asyncio.Task | None = None
    last_watch_spoken_media_time: float = -1.0
    vision_error_notified: bool = False
    watch_epoch: int = 0
    watch_ending_epoch: int = -1
    shared_experience_task: asyncio.Task | None = None
    shared_experience_finalized: bool = False
    shared_experience_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    integration_closed: bool = False
    generation_task: asyncio.Task | None = None
    resume_token: str = ""
    detach_close_task: asyncio.Task | None = None
    last_activity_notify: float = 0.0
    call_active: bool = False
    call_last_user_activity: float = 0.0
    call_last_proactive_at: float = 0.0
    call_camera_frame: str = ""
    call_camera_updated_at: float = 0.0
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    conversation_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    astrbot_unified_msg_origin: str = ""
    astrbot_conversation_id: str = ""

    def append_turn(self, role: str, content: str, *, history_turns: int) -> None:
        text = str(content or "").strip()
        if not text:
            return
        self.history.append({"role": role, "content": text})
        max_messages = max(4, int(history_turns or 12) * 2)
        if len(self.history) > max_messages:
            self.history = self.history[-max_messages:]

    def cancel_generation(self) -> bool:
        task = self.generation_task
        if not isinstance(task, asyncio.Task) or task.done():
            return False
        task.cancel()
        return True

    def cancel_media_resolution(self) -> bool:
        task = self.media_resolution_task
        self.media_resolution_task = None
        if not isinstance(task, asyncio.Task) or task.done():
            return False
        task.cancel()
        return True

    def update_call_camera(self, frame: str) -> None:
        self.call_camera_frame = str(frame or "")
        self.call_camera_updated_at = time.monotonic() if self.call_camera_frame else 0.0

    def recent_call_camera_frame(self, *, max_age_seconds: float = 25.0) -> str:
        if not self.call_active or self.mode != "call" or not self.call_camera_frame:
            return ""
        age = time.monotonic() - float(self.call_camera_updated_at or 0.0)
        return self.call_camera_frame if age <= max(1.0, float(max_age_seconds)) else ""

    def append_watch_event(
        self,
        kind: str,
        text: str,
        *,
        media_time: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        content = str(text or "").strip()
        if not content:
            return None
        self.watch_event_sequence += 1
        event = {
            "seq": self.watch_event_sequence,
            "kind": str(kind or "event")[:40],
            "text": content[:600],
            "media_time": max(0.0, float(media_time or 0.0)),
            "created_at": time.time(),
            "metadata": dict(metadata or {}),
        }
        self.watch_events.append(event)
        if len(self.watch_events) > 140:
            self.watch_events = self.watch_events[-140:]
        return event

    def reset_watch(self, *, media_token: str = "") -> None:
        self.cancel_watch_knowledge()
        self.cancel_watch_memory()
        self.media_token = str(media_token or "")
        self.watch_events.clear()
        self.watch_event_sequence = 0
        self.watch_knowledge = ""
        self.watch_memory = ""
        self.watch_memory_cursor = 0
        self.watch_memory_media_time = 0.0
        self.last_watch_spoken_media_time = -1.0
        self.vision_error_notified = False
        self.watch_epoch += 1

    def cancel_watch_knowledge(self) -> bool:
        task = self.watch_knowledge_task
        self.watch_knowledge_task = None
        if not isinstance(task, asyncio.Task) or task.done():
            return False
        task.cancel()
        return True

    def cancel_watch_memory(self) -> bool:
        task = self.watch_memory_task
        self.watch_memory_task = None
        self.watch_memory_refreshing = False
        if not isinstance(task, asyncio.Task) or task.done():
            return False
        task.cancel()
        return True
