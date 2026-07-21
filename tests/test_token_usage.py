# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from astrbot_stubs import install_astrbot_stubs

install_astrbot_stubs()

from astrbot_plugin_together_companion.main import TogetherCompanionPlugin
from astrbot_plugin_together_companion.token_usage import TokenUsageTracker


class TokenUsageTrackerTests(unittest.TestCase):
    def test_real_provider_usage_is_aggregated_and_persisted(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "usage.json"
            tracker = TokenUsageTracker(path)
            response = SimpleNamespace(
                usage={"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150}
            )

            tracker.record(
                provider_id="vision-a",
                task="together_watch_comment",
                prompt="当前画面",
                completion="这幕很安静。",
                elapsed_ms=240,
                success=True,
                response=response,
            )
            tracker.save(force=True)

            summary = TokenUsageTracker(path).summary()
            self.assertEqual(150, summary["totals"]["total_tokens"])
            self.assertEqual(150, summary["by_provider"]["vision-a"]["total_tokens"])
            self.assertEqual(1, summary["by_task"]["together_watch_comment"]["calls"])
            self.assertFalse(summary["recent"][-1]["estimated"])

    def test_missing_usage_uses_estimate(self) -> None:
        with TemporaryDirectory() as directory:
            tracker = TokenUsageTracker(Path(directory) / "usage.json")
            tracker.record(
                provider_id="chat-a",
                task="together_realtime_reply",
                prompt="陪我聊一会儿",
                completion="好，我在。",
                elapsed_ms=10,
                success=True,
            )

            summary = tracker.summary()
            self.assertGreater(summary["totals"]["total_tokens"], 0)
            self.assertTrue(summary["recent"][-1]["estimated"])


class TrackedProviderCallTests(unittest.IsolatedAsyncioTestCase):
    async def test_tracked_call_records_task_and_provider(self) -> None:
        class Provider:
            def meta(self):
                return {"id": "provider-a"}

            async def text_chat(self, *, prompt, system_prompt):
                return SimpleNamespace(
                    completion_text="整理完成",
                    usage={"input_tokens": 20, "output_tokens": 5, "total_tokens": 25},
                )

        with TemporaryDirectory() as directory:
            plugin = TogetherCompanionPlugin.__new__(TogetherCompanionPlugin)
            plugin.token_usage = TokenUsageTracker(Path(directory) / "usage.json")

            await plugin._tracked_text_chat(
                Provider(),
                task="together_watch_knowledge",
                prompt="来源材料",
                system_prompt="无剧透整理",
                timeout=1,
            )

            summary = plugin.token_usage.summary()
            self.assertEqual(25, summary["by_task"]["together_watch_knowledge"]["total_tokens"])
            self.assertIn("provider-a", summary["by_provider"])


if __name__ == "__main__":
    unittest.main()
