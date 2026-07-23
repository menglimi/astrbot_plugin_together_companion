# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import tempfile
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

    def test_room_scripts_do_not_depend_on_external_cdn(self) -> None:
        policy = self._server()._security_headers("text/html")["Content-Security-Policy"]
        page = (Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(encoding="utf-8")

        self.assertIn("script-src 'self'", policy)
        self.assertNotIn("unpkg.com", policy)
        self.assertIn('/assets/lucide.min.js', page)
        self.assertNotIn("https://unpkg.com", page)

    def test_packaged_resources_fallback_when_filesystem_web_root_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            missing_root = Path(temporary) / "missing-web"
            server = TogetherRoomServer(
                SimpleNamespace(plugin_root=Path(temporary) / "missing-plugin"),
                host="127.0.0.1",
                port=6321,
                web_root=missing_root,
                resource_package="astrbot_plugin_together_companion",
            )

            self.assertIsNone(server._filesystem_web_asset("index.html"))
            self.assertIn(b'class="app-shell"', server._packaged_web_asset("index.html"))
            self.assertTrue(all(server._web_asset_available(name) for name in server.REQUIRED_WEB_ASSETS))

    def test_nested_docker_plugin_layout_is_discovered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plugin_root = Path(temporary)
            nested_web = plugin_root / "astrbot_plugin_together_companion-main" / "web"
            nested_web.mkdir(parents=True)
            nested_index = nested_web / "index.html"
            nested_index.write_text("<!doctype html>", encoding="utf-8")
            server = TogetherRoomServer(
                SimpleNamespace(plugin_root=plugin_root),
                host="127.0.0.1",
                port=6321,
                web_root=plugin_root / "missing-web",
            )

            self.assertEqual(nested_index, server._filesystem_web_asset("index.html"))

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
