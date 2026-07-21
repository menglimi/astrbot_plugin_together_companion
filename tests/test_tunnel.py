# -*- coding: utf-8 -*-
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from astrbot_stubs import install_astrbot_stubs

install_astrbot_stubs()

from astrbot_plugin_together_companion.tunnel import CloudflareQuickTunnel, QUICK_TUNNEL_URL


class QuickTunnelTests(unittest.IsolatedAsyncioTestCase):
    def test_quick_tunnel_url_is_extracted_from_cloudflared_log(self) -> None:
        line = "INF Your quick Tunnel has been created! Visit https://quiet-river.trycloudflare.com"

        match = QUICK_TUNNEL_URL.search(line)

        self.assertIsNotNone(match)
        self.assertEqual("https://quiet-river.trycloudflare.com", match.group(0))

    def test_bundled_cloudflared_is_detected_without_path_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            binary = root / "cloudflared.exe"
            binary.write_bytes(b"test")
            tunnel = CloudflareQuickTunnel(local_url="http://127.0.0.1:6321", search_paths=[root])

            with patch("astrbot_plugin_together_companion.tunnel.shutil.which", return_value=None):
                self.assertEqual(binary, tunnel.binary_path())
                self.assertTrue(tunnel.status()["installed"])

    async def test_stop_is_idempotent_when_not_running(self) -> None:
        tunnel = CloudflareQuickTunnel(local_url="http://127.0.0.1:6321")

        await tunnel.stop()
        await tunnel.stop()

        self.assertFalse(tunnel.running)
        self.assertEqual("", tunnel.url)

    def test_status_distinguishes_allocated_and_reachable(self) -> None:
        tunnel = CloudflareQuickTunnel(local_url="http://127.0.0.1:6321")
        tunnel._process = type("Process", (), {"returncode": None})()
        tunnel.url = "https://quiet-river.trycloudflare.com"

        self.assertTrue(tunnel.status()["running"])
        self.assertFalse(tunnel.status()["ready"])

        tunnel._reachable = True
        self.assertTrue(tunnel.status()["ready"])


if __name__ == "__main__":
    unittest.main()
