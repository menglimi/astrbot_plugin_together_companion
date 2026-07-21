# -*- coding: utf-8 -*-
from __future__ import annotations

from types import SimpleNamespace
import unittest

from astrbot_stubs import install_astrbot_stubs

install_astrbot_stubs()

from astrbot_plugin_together_companion.server import TogetherRoomServer


class RoomOriginTests(unittest.TestCase):
    @staticmethod
    def _server(public_base_url: str = "") -> TogetherRoomServer:
        server = TogetherRoomServer.__new__(TogetherRoomServer)
        server.plugin = SimpleNamespace(public_base_url=public_base_url)
        return server

    @staticmethod
    def _request(origin: str, *, scheme: str = "http", host: str = "127.0.0.1:6321"):
        return SimpleNamespace(headers={"Origin": origin}, scheme=scheme, host=host)

    def test_exact_same_origin_is_allowed(self) -> None:
        request = self._request("http://127.0.0.1:6321")
        self.assertTrue(self._server()._origin_allowed(request))

    def test_room_permissions_allow_same_origin_camera_and_microphone(self) -> None:
        policy = self._server()._security_headers()["Permissions-Policy"]

        self.assertIn("camera=(self)", policy)
        self.assertIn("microphone=(self)", policy)

    def test_different_local_origin_is_rejected(self) -> None:
        request = self._request("http://localhost:9000")
        self.assertFalse(self._server()._origin_allowed(request))

    def test_configured_public_origin_is_allowed_behind_proxy(self) -> None:
        request = self._request(
            "https://together.example.com",
            scheme="http",
            host="127.0.0.1:6321",
        )
        self.assertTrue(
            self._server("https://together.example.com/room")._origin_allowed(request)
        )

    def test_public_origin_with_wrong_port_is_rejected(self) -> None:
        request = self._request("https://together.example.com:8443")
        self.assertFalse(
            self._server("https://together.example.com")._origin_allowed(request)
        )

    def test_running_quick_tunnel_origin_is_allowed_exactly(self) -> None:
        server = self._server()
        server.plugin.quick_tunnel = SimpleNamespace(
            running=True,
            url="https://quiet-river.trycloudflare.com",
        )
        request = self._request(
            "https://quiet-river.trycloudflare.com",
            scheme="http",
            host="quiet-river.trycloudflare.com",
        )

        self.assertTrue(server._origin_allowed(request))
        self.assertFalse(
            server._origin_allowed(
                self._request("https://other.trycloudflare.com", scheme="http", host=request.host)
            )
        )


if __name__ == "__main__":
    unittest.main()
