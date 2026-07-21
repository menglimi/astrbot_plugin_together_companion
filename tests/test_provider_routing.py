# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from types import SimpleNamespace

from astrbot_stubs import install_astrbot_stubs

install_astrbot_stubs()

from astrbot_plugin_together_companion.main import TogetherCompanionPlugin


class _Provider:
    def __init__(self, provider_id: str, modalities: list[str]) -> None:
        self.provider_config = {
            "id": provider_id,
            "model": provider_id,
            "modalities": modalities,
        }

    async def text_chat(self, **_kwargs):
        return SimpleNamespace(completion_text="ok")


class _Context:
    def __init__(self, providers: dict[str, _Provider], chat_id: str, image_id: str) -> None:
        self.providers = providers
        self.chat_id = chat_id
        self.image_id = image_id

    def get_provider_by_id(self, provider_id: str):
        return self.providers.get(provider_id)

    def get_using_provider(self):
        return self.providers.get(self.chat_id)

    def get_config(self):
        return {
            "provider_settings": {
                "default_image_caption_provider_id": self.image_id,
            }
        }


def _plugin(context: _Context, *, vision_provider_id: str = "") -> TogetherCompanionPlugin:
    plugin = TogetherCompanionPlugin.__new__(TogetherCompanionPlugin)
    plugin.context = context
    plugin.chat_provider_id = ""
    plugin.vision_provider_id = vision_provider_id
    return plugin


class ProviderRoutingTests(unittest.TestCase):
    def test_global_image_caption_provider_is_used_before_text_chat_model(self) -> None:
        chat = _Provider("chat", ["text"])
        vision = _Provider("vision", ["text", "image"])
        plugin = _plugin(_Context({"chat": chat, "vision": vision}, "chat", "vision"))

        self.assertIs(vision, plugin._get_vision_provider())

    def test_explicit_vision_provider_has_priority(self) -> None:
        chat = _Provider("chat", ["text"])
        global_vision = _Provider("global-vision", ["image"])
        explicit_vision = _Provider("explicit-vision", ["image"])
        plugin = _plugin(
            _Context(
                {
                    "chat": chat,
                    "global-vision": global_vision,
                    "explicit-vision": explicit_vision,
                },
                "chat",
                "global-vision",
            ),
            vision_provider_id="explicit-vision",
        )

        self.assertIs(explicit_vision, plugin._get_vision_provider())

    def test_text_only_candidates_do_not_claim_vision_availability(self) -> None:
        chat = _Provider("chat", ["text"])
        plugin = _plugin(_Context({"chat": chat}, "chat", ""))

        self.assertIsNone(plugin._get_vision_provider())


if __name__ == "__main__":
    unittest.main()
