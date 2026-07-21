# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from types import SimpleNamespace

from astrbot_stubs import install_astrbot_stubs

install_astrbot_stubs()

from astrbot_plugin_together_companion.main import TogetherCompanionPlugin
from astrbot_plugin_together_companion.models import RoomSession


class _Provider:
    def __init__(self, provider_id: str, modalities: list[str] | None) -> None:
        self.provider_config = {
            "id": provider_id,
            "model": provider_id,
        }
        if modalities is not None:
            self.provider_config["modalities"] = modalities
        self.calls = []

    async def text_chat(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(completion_text=f"{self.provider_config['id']}-ok")


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
    plugin.chat_provider_id = context.chat_id
    plugin.vision_provider_id = vision_provider_id
    return plugin


class ProviderRoutingTests(unittest.TestCase):
    def test_chat_provider_must_be_selected_explicitly(self) -> None:
        default_chat = _Provider("default-chat", ["text"])
        context = _Context({"default-chat": default_chat}, "default-chat", "")
        plugin = _plugin(context)
        plugin.chat_provider_id = ""

        self.assertIsNone(plugin._get_chat_provider())

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

    def test_multimodal_chat_provider_has_priority_over_explicit_visual_fallback(self) -> None:
        chat = _Provider("multimodal-chat", ["text", "image"])
        explicit_vision = _Provider("explicit-vision", ["text", "image"])
        plugin = _plugin(
            _Context(
                {"multimodal-chat": chat, "explicit-vision": explicit_vision},
                "multimodal-chat",
                "",
            ),
            vision_provider_id="explicit-vision",
        )

        self.assertIs(chat, plugin._get_vision_provider())

    def test_multimodal_chat_provider_is_preferred_over_image_caption_provider(self) -> None:
        chat = _Provider("multimodal-chat", ["text", "image"])
        image_default = _Provider("image-caption", ["text", "image"])
        plugin = _plugin(
            _Context(
                {"multimodal-chat": chat, "image-caption": image_default},
                "multimodal-chat",
                "image-caption",
            )
        )

        self.assertIs(chat, plugin._get_vision_provider())

    def test_chat_provider_without_capability_metadata_is_last_resort_vision_candidate(self) -> None:
        chat = _Provider("legacy-multimodal-chat", None)
        plugin = _plugin(_Context({"legacy-multimodal-chat": chat}, "legacy-multimodal-chat", ""))

        self.assertIs(chat, plugin._get_vision_provider())

    def test_text_only_candidates_do_not_claim_vision_availability(self) -> None:
        chat = _Provider("chat", ["text"])
        plugin = _plugin(_Context({"chat": chat}, "chat", ""))

        self.assertIsNone(plugin._get_vision_provider())


class MultimodalConversationTests(unittest.IsolatedAsyncioTestCase):
    async def test_separate_visual_model_transcribes_before_chat_reply(self) -> None:
        chat = _Provider("chat", ["text"])
        vision = _Provider("vision", ["text", "image"])
        plugin = _plugin(
            _Context({"chat": chat, "vision": vision}, "chat", ""),
            vision_provider_id="vision",
        )
        plugin.token_usage = None

        async def system_prompt(*_args, **_kwargs):
            return "system"

        plugin._build_system_prompt = system_prompt
        room = RoomSession("room", "ticket", "call", "user", None)

        result = await plugin._generate_model_text(
            room,
            "你看看我手里这个",
            image_data_url="data:image/jpeg;base64,/9g=",
        )

        self.assertEqual("chat-ok", result)
        self.assertEqual(1, len(vision.calls))
        self.assertEqual(["data:image/jpeg;base64,/9g="], vision.calls[0]["image_urls"])
        self.assertEqual(1, len(chat.calls))
        self.assertIsNone(chat.calls[0].get("image_urls"))
        self.assertIn("vision-ok", chat.calls[0]["prompt"])

    async def test_multimodal_chat_receives_camera_frame_directly(self) -> None:
        chat = _Provider("multimodal-chat", ["text", "image"])
        plugin = _plugin(_Context({"multimodal-chat": chat}, "multimodal-chat", ""))
        plugin.token_usage = None

        async def system_prompt(*_args, **_kwargs):
            return "system"

        plugin._build_system_prompt = system_prompt
        room = RoomSession("room", "ticket", "call", "user", None)

        await plugin._generate_model_text(
            room,
            "现在能看到吗",
            image_data_url="data:image/jpeg;base64,/9g=",
        )

        self.assertEqual(1, len(chat.calls))
        self.assertEqual(["data:image/jpeg;base64,/9g="], chat.calls[0]["image_urls"])


if __name__ == "__main__":
    unittest.main()
