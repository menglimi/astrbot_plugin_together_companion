# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import sys
import tempfile
import types
from pathlib import Path


def install_astrbot_stubs() -> None:
    plugins_root = Path(__file__).resolve().parents[2]
    if str(plugins_root) not in sys.path:
        sys.path.insert(0, str(plugins_root))
    if "astrbot.api" in sys.modules:
        return

    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    event = types.ModuleType("astrbot.api.event")
    star = types.ModuleType("astrbot.api.star")
    web = types.ModuleType("astrbot.api.web")
    core = types.ModuleType("astrbot.core")
    utils = types.ModuleType("astrbot.core.utils")
    paths = types.ModuleType("astrbot.core.utils.astrbot_path")

    class AstrBotConfig(dict):
        pass

    class AstrMessageEvent:
        pass

    class Star:
        def __init__(self, context=None):
            self.context = context

    class StarTools:
        @staticmethod
        def get_data_dir(_name):
            return Path(tempfile.gettempdir()) / "together_companion_tests"

    class PermissionType:
        ADMIN = "admin"

    class Filter:
        @staticmethod
        def permission_type(*_args, **_kwargs):
            return lambda target: target

        @staticmethod
        def command(*_args, **_kwargs):
            return lambda target: target

        @staticmethod
        def llm_tool(*_args, **_kwargs):
            return lambda target: target

    Filter.PermissionType = PermissionType

    def register(*_args, **_kwargs):
        return lambda target: target

    api.AstrBotConfig = AstrBotConfig
    api.logger = logging.getLogger("together-tests")
    event.AstrMessageEvent = AstrMessageEvent
    event.filter = Filter()
    star.Context = object
    star.Star = Star
    star.StarTools = StarTools
    star.register = register
    web.request = types.SimpleNamespace()
    paths.get_astrbot_data_path = lambda: str(Path(tempfile.gettempdir()))

    astrbot.api = api
    sys.modules.update(
        {
            "astrbot": astrbot,
            "astrbot.api": api,
            "astrbot.api.event": event,
            "astrbot.api.star": star,
            "astrbot.api.web": web,
            "astrbot.core": core,
            "astrbot.core.utils": utils,
            "astrbot.core.utils.astrbot_path": paths,
        }
    )
