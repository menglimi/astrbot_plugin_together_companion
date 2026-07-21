# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any


class TokenUsageTracker:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._last_save_at = 0.0
        self._usage = self._load()

    @staticmethod
    def _safe_int(value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _estimate(text: str) -> int:
        raw = str(text or "")
        if not raw:
            return 0
        ascii_chars = sum(1 for char in raw if ord(char) < 128)
        non_ascii_chars = len(raw) - ascii_chars
        return max(1, int(ascii_chars / 4.0 + non_ascii_chars / 1.6))

    @staticmethod
    def _raw_value(value: Any, key: str) -> Any:
        current = value
        for part in key.split("."):
            if isinstance(current, dict):
                current = current.get(part)
            else:
                current = getattr(current, part, None)
            if current is None:
                return None
        return current

    @classmethod
    def _usage_value(cls, usage: Any, *keys: str) -> int:
        for key in keys:
            parsed = cls._safe_int(cls._raw_value(usage, key))
            if parsed > 0:
                return parsed
        return 0

    @classmethod
    def _extract(cls, response: Any, prompt: str, completion: str) -> dict[str, Any]:
        candidates = [
            getattr(response, "usage", None),
            getattr(response, "token_usage", None),
            getattr(response, "raw_usage", None),
        ]
        raw_completion = getattr(response, "raw_completion", None)
        if raw_completion is not None:
            candidates.append(getattr(raw_completion, "usage", None))
        raw_response = getattr(response, "raw_response", None)
        if isinstance(raw_response, dict):
            candidates.extend((raw_response.get("usage"), raw_response.get("token_usage")))
        usage = next((candidate for candidate in candidates if candidate), None)
        prompt_tokens = cls._usage_value(usage, "prompt_tokens", "input_tokens", "prompt", "input")
        completion_tokens = cls._usage_value(
            usage,
            "completion_tokens",
            "output_tokens",
            "completion",
            "output",
        )
        total_tokens = cls._usage_value(usage, "total_tokens", "total")
        cached_tokens = cls._usage_value(
            usage,
            "cached_tokens",
            "input_cached",
            "prompt_tokens_details.cached_tokens",
            "input_tokens_details.cached_tokens",
        )
        cache_read_tokens = cls._usage_value(
            usage,
            "cache_read_tokens",
            "cache_read_input_tokens",
            "input_token_details.cache_read",
            "prompt_cache_hit_tokens",
        ) or cached_tokens
        cache_write_tokens = cls._usage_value(
            usage,
            "cache_write_tokens",
            "cache_creation_input_tokens",
            "cache_creation_tokens",
            "prompt_cache_creation_tokens",
        )
        estimated = False
        if total_tokens <= 0:
            prompt_estimated = prompt_tokens <= 0
            completion_estimated = completion_tokens <= 0
            if prompt_estimated:
                prompt_tokens = cls._estimate(prompt)
            if completion_estimated:
                completion_tokens = cls._estimate(completion)
            total_tokens = prompt_tokens + completion_tokens
            estimated = not usage or prompt_estimated or completion_estimated
        elif prompt_tokens <= 0 and completion_tokens <= 0:
            prompt_tokens = min(total_tokens, cls._estimate(prompt))
            completion_tokens = max(0, total_tokens - prompt_tokens)
            estimated = True
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "cached_tokens": cached_tokens,
            "cache_read_tokens": cache_read_tokens,
            "cache_write_tokens": cache_write_tokens,
            "estimated": estimated,
        }

    def record(
        self,
        *,
        provider_id: str,
        task: str,
        prompt: str,
        completion: str,
        elapsed_ms: int,
        success: bool,
        error: str = "",
        response: Any = None,
    ) -> None:
        usage = self._extract(response, prompt, completion)
        now = datetime.now()
        now_ts = time.time()
        day = now.strftime("%Y-%m-%d")
        hour = now.strftime("%Y-%m-%d %H:00")
        store = self._usage if isinstance(self._usage, dict) else {}
        self._usage = store
        provider_key = str(provider_id or "(default)")[:160]
        task_key = str(task or "together_other")[:80]

        def mapping(name: str) -> dict[str, Any]:
            value = store.setdefault(name, {})
            if not isinstance(value, dict):
                value = {}
                store[name] = value
            return value

        def nested(parent: dict[str, Any], key: str) -> dict[str, Any]:
            value = parent.setdefault(key, {})
            if not isinstance(value, dict):
                value = {}
                parent[key] = value
            return value

        def bump(bucket: dict[str, Any]) -> None:
            bucket["calls"] = self._safe_int(bucket.get("calls")) + 1
            bucket["success"] = self._safe_int(bucket.get("success")) + (1 if success else 0)
            bucket["errors"] = self._safe_int(bucket.get("errors")) + (0 if success else 1)
            for key in (
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "cached_tokens",
                "cache_read_tokens",
                "cache_write_tokens",
            ):
                bucket[key] = self._safe_int(bucket.get(key)) + self._safe_int(usage.get(key))
            bucket["estimated_tokens"] = self._safe_int(bucket.get("estimated_tokens")) + (
                usage["total_tokens"] if usage["estimated"] else 0
            )
            bucket["elapsed_ms"] = self._safe_int(bucket.get("elapsed_ms")) + max(0, int(elapsed_ms or 0))
            bucket["last_ts"] = now_ts

        totals = mapping("totals")
        by_provider = mapping("by_provider")
        by_task = mapping("by_task")
        by_day = mapping("by_day")
        by_day_provider = mapping("by_day_provider")
        by_day_task = mapping("by_day_task")
        by_hour = mapping("by_hour")
        day_providers = nested(by_day_provider, day)
        day_tasks = nested(by_day_task, day)
        for bucket in (
            totals,
            by_provider.setdefault(provider_key, {}),
            by_task.setdefault(task_key, {}),
            by_day.setdefault(day, {}),
            day_providers.setdefault(provider_key, {}),
            day_tasks.setdefault(task_key, {}),
            by_hour.setdefault(hour, {}),
        ):
            if isinstance(bucket, dict):
                bump(bucket)

        recent = store.setdefault("recent", [])
        if not isinstance(recent, list):
            recent = []
            store["recent"] = recent
        recent.append(
            {
                "ts": now_ts,
                "time": now.strftime("%Y-%m-%d %H:%M:%S"),
                "provider": provider_key,
                "task": task_key,
                "success": bool(success),
                **usage,
                "elapsed_ms": max(0, int(elapsed_ms or 0)),
                "prompt_chars": len(str(prompt or "")),
                "completion_chars": len(str(completion or "")),
                "error": " ".join(str(error or "").split())[:160],
            }
        )
        del recent[:-240]
        store["updated_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
        self.save()

    def summary(self) -> dict[str, Any]:
        try:
            payload = json.loads(json.dumps(self._usage, ensure_ascii=False))
        except (TypeError, ValueError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        payload.update(
            {
                "available": True,
                "display_name": "我会和你在一起",
                "plugin_name": "astrbot_plugin_together_companion",
                "counted_in_private_companion_budget": False,
                "note": "展示实时通话、画面理解和观影整理的模型消耗，不计入陪伴插件每日 Token 限额。",
            }
        )
        return payload

    def _load(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, TypeError, ValueError):
            return {}

    def save(self, *, force: bool = False) -> None:
        now_ts = time.time()
        if not force and now_ts - self._last_save_at < 30:
            return
        self._last_save_at = now_ts
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(self._usage, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(self.path)
        except OSError:
            return
