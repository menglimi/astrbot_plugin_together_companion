# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from astrbot_stubs import install_astrbot_stubs


install_astrbot_stubs()

from astrbot_plugin_together_companion.main import TogetherCompanionPlugin
from astrbot_plugin_together_companion.models import RoomSession, normalize_room_mode


ROOT = Path(__file__).resolve().parents[1]


class WorkCollaborationTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _plugin(api=None) -> TogetherCompanionPlugin:
        plugin = TogetherCompanionPlugin.__new__(TogetherCompanionPlugin)
        plugin._screen_companion_api = lambda: api
        return plugin

    def test_work_mode_is_a_first_class_room_mode(self) -> None:
        self.assertEqual("work", normalize_room_mode(" work "))

    def test_capability_requires_compatible_screen_companion_api(self) -> None:
        missing = self._plugin()
        incompatible = self._plugin(SimpleNamespace())
        compatible = self._plugin(
            SimpleNamespace(get_work_collaboration_context=lambda **_kwargs: {})
        )

        self.assertFalse(missing.work_collaboration_available())
        self.assertFalse(incompatible.work_collaboration_available())
        self.assertEqual({"available": False, "label": ""}, missing._work_collaboration_capability())
        self.assertTrue(compatible.work_collaboration_available())
        self.assertTrue(compatible._work_collaboration_capability()["available"])

    async def test_missing_screen_companion_degrades_to_empty_context(self) -> None:
        plugin = self._plugin()
        room = RoomSession("room", "ticket", "work", "995051631", None)

        context = await plugin._work_collaboration_context(room, force=True)

        self.assertEqual({}, context)
        self.assertEqual({}, room.work_context)
        self.assertGreater(room.work_context_updated_at, 0)

    async def test_compatible_api_returns_normalized_privacy_reduced_context(self) -> None:
        getter = AsyncMock(
            return_value={
                "available": True,
                "context_available": True,
                "privacy_masked": True,
                "tracking_enabled": True,
                "current": {
                    "type": "coding",
                    "scene": "开发",
                    "app_name": "Visual Studio Code",
                    "window": "main.py\n敏感换行",
                    "resource_label": "Together Companion",
                    "duration_seconds": 180,
                },
                "observation": {
                    "summary": "正在检查工作协同接口。",
                    "scene": "coding",
                    "age_seconds": 4,
                },
            }
        )
        plugin = self._plugin(SimpleNamespace(get_work_collaboration_context=getter))
        room = RoomSession("room", "ticket", "work", "995051631", None)

        context = await plugin._work_collaboration_context(room, force=True)

        getter.assert_awaited_once_with(user_id="995051631")
        self.assertTrue(context["available"])
        self.assertTrue(context["context_available"])
        self.assertTrue(context["privacy_masked"])
        self.assertEqual("main.py 敏感换行", context["current"]["window"])
        self.assertEqual("正在检查工作协同接口。", context["observation"]["summary"])

    async def test_work_prompt_stays_usable_without_screen_plugin(self) -> None:
        plugin = self._plugin()
        plugin.enable_memory_context = False
        plugin.custom_system_prompt = ""
        plugin.model_hangup_enabled = False
        plugin._companion_scene_cached = lambda _user_id: {}

        async def persona_prompt():
            return "你是测试人格。"

        plugin._persona_prompt_cached = persona_prompt
        room = RoomSession("room", "ticket", "work", "995051631", None)

        prompt = await plugin._build_system_prompt(room, query="继续处理当前任务")

        self.assertIn("工作协同", prompt)
        self.assertIn("文字协同已经可用", prompt)
        self.assertNotIn("屏幕伙伴提供的当前工作上下文", prompt)

    def test_work_state_is_normalized_and_limited(self) -> None:
        plugin = self._plugin()
        state = plugin._normalize_work_state(
            {
                "goal": "完成登录页联调",
                "status": "IN_PROGRESS",
                "success_criteria": ["登录成功", "登录成功", "第三项", "忽略这一项"],
                "current_step": "检查接口返回",
                "progress": "已经定位到请求参数问题",
                "blockers": ["缺少测试账号", "缺少测试账号"],
                "next_action": "请提供测试账号或脱敏响应",
                "evidence": "日志显示 401",
            }
        )

        self.assertEqual("in_progress", state["status"])
        self.assertEqual(["登录成功", "第三项", "忽略这一项"], state["success_criteria"])
        self.assertEqual(["缺少测试账号"], state["blockers"])

    def test_work_state_marker_is_removed_from_visible_reply(self) -> None:
        plugin = self._plugin()
        visible, state = plugin._extract_work_state(
            "已定位问题，下一步请补充测试账号。\n"
            '<together-work-state>{"goal":"完成联调","status":"blocked",'
            '"blockers":["缺少测试账号"],"next_action":"提供脱敏响应"}</together-work-state>'
        )

        self.assertEqual("已定位问题，下一步请补充测试账号。", visible)
        self.assertEqual("blocked", state["status"])
        self.assertEqual(["缺少测试账号"], state["blockers"])

        malformed_visible, malformed_state = plugin._extract_work_state(
            "正常可见回复\n<together-work-state>{not-json}"
        )
        self.assertEqual("正常可见回复", malformed_visible)
        self.assertEqual({}, malformed_state)

    def test_progress_check_only_starts_after_relevant_context_change(self) -> None:
        plugin = self._plugin()
        scheduled = []
        plugin._start_room_task = lambda _room, operation: scheduled.append(operation)
        room = RoomSession("room", "ticket", "work", "995051631", None)
        room.work_state = {"goal": "完成联调", "status": "in_progress"}
        first = {
            "context_available": True,
            "current": {"app_name": "VS Code", "window": "main.py"},
            "observation": {"summary": "正在修改接口", "observed_at": 10},
        }
        changed = {
            "context_available": True,
            "current": {"app_name": "Browser", "window": "测试页"},
            "observation": {"summary": "页面显示登录成功", "observed_at": 20},
        }

        self.assertFalse(plugin._maybe_start_work_progress_check(room, first))
        self.assertFalse(plugin._maybe_start_work_progress_check(room, first))
        self.assertTrue(plugin._maybe_start_work_progress_check(room, changed))
        self.assertEqual(1, len(scheduled))
        scheduled[0].close()

    async def test_silent_progress_check_updates_state_without_chat_message(self) -> None:
        plugin = self._plugin()
        plugin.history_turns = 12
        plugin._generate_model_text = AsyncMock(
            return_value=(
                "[SILENT]\n"
                '<together-work-state>{"goal":"完成联调","status":"completed",'
                '"progress":"测试通过","evidence":"页面显示登录成功"}</together-work-state>'
            )
        )
        plugin.send_room_payload = AsyncMock()
        room = RoomSession("room", "ticket", "work", "995051631", None)
        room.work_state = {"goal": "完成联调", "status": "in_progress"}

        await plugin._generate_work_progress_check(room)

        self.assertEqual("completed", room.work_state["status"])
        plugin.send_room_payload.assert_awaited_once()
        self.assertEqual("work_state", plugin.send_room_payload.await_args.args[1]["type"])

    async def test_work_prompt_includes_execution_state_guidance(self) -> None:
        plugin = self._plugin()
        plugin.enable_memory_context = False
        plugin.custom_system_prompt = ""
        plugin.model_hangup_enabled = False
        plugin._companion_scene_cached = lambda _user_id: {}
        plugin._persona_prompt_cached = AsyncMock(return_value="你是测试人格。")
        room = RoomSession("room", "ticket", "work", "995051631", None)
        room.work_state = {
            "goal": "完成登录页联调",
            "status": "in_progress",
            "next_action": "检查接口返回",
        }

        prompt = await plugin._build_system_prompt(room, query="继续")

        self.assertIn("完成登录页联调", prompt)
        self.assertIn("只有用户确认、屏幕观察明确显示", prompt)
        self.assertIn("together-work-state", prompt)

    def test_web_room_and_launcher_hide_work_until_capability_is_available(self) -> None:
        room_page = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        room_script = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        launcher_page = (ROOT / "pages" / "一起房间" / "index.html").read_text(encoding="utf-8")
        launcher_script = (ROOT / "pages" / "一起房间" / "launcher.js").read_text(
            encoding="utf-8"
        )

        self.assertRegex(
            room_page,
            r'<button[^>]+data-mode-tab="work"[^>]+hidden(?:\s|>)',
        )
        self.assertIn('id="workView"', room_page)
        self.assertIn('id="workTranscript"', room_page)
        self.assertIn('id="refreshWorkContext"', room_page)
        self.assertIn('id="workGoal"', room_page)
        self.assertIn('id="workNextAction"', room_page)
        self.assertIn('state.room?.work?.available !== true', room_script)
        self.assertIn('["call", "watch", "work"].includes(mode)', room_script)
        self.assertIn('case "work_state"', room_script)
        self.assertIn('applyWorkState(room.work?.state || {})', room_script)
        self.assertRegex(
            launcher_page,
            r'<button[^>]+data-mode="work"[^>]+hidden(?:\s|>)',
        )
        self.assertIn('data?.capabilities?.work?.available === true', launcher_script)
        self.assertIn('workButton.hidden = !workAvailable', launcher_script)


if __name__ == "__main__":
    unittest.main()
