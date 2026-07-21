# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import aiohttp


QUICK_TUNNEL_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com", re.IGNORECASE)


class CloudflareQuickTunnel:
    def __init__(self, *, local_url: str, search_paths: list[Path] | None = None) -> None:
        self.local_url = str(local_url or "").rstrip("/")
        self.search_paths = [Path(item) for item in (search_paths or [])]
        self.url = ""
        self.started_at = 0.0
        self.error = ""
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._probe_task: asyncio.Task | None = None
        self._ready = asyncio.Event()
        self._reachable = False
        self._lock = asyncio.Lock()

    def binary_path(self) -> Path | None:
        names = ("cloudflared.exe", "cloudflared") if os.name == "nt" else ("cloudflared",)
        command = shutil.which("cloudflared")
        if command:
            return Path(command)
        for root in self.search_paths:
            candidates = [root] if root.suffix else [root / name for name in names]
            for candidate in candidates:
                if candidate.is_file():
                    return candidate
        return None

    @property
    def running(self) -> bool:
        process = self._process
        return bool(process is not None and process.returncode is None and self.url)

    def status(self) -> dict[str, Any]:
        binary = self.binary_path()
        return {
            "installed": binary is not None,
            "running": self.running,
            "ready": self.running and self._reachable,
            "url": self.url if self.running else "",
            "started_at": self.started_at if self.running else 0.0,
            "error": self.error,
        }

    async def start(self, *, timeout: float = 35.0) -> str:
        async with self._lock:
            if self.running:
                return self.url
            await self._stop_locked()
            binary = self.binary_path()
            if binary is None:
                raise RuntimeError("未找到 cloudflared，请先安装 Cloudflare Tunnel 客户端")
            self.url = ""
            self.error = ""
            self.started_at = 0.0
            self._reachable = False
            self._ready = asyncio.Event()
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            try:
                self._process = await asyncio.create_subprocess_exec(
                    str(binary),
                    "tunnel",
                    "--no-autoupdate",
                    "--url",
                    self.local_url,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    creationflags=creationflags,
                )
            except OSError as exc:
                self._process = None
                raise RuntimeError(f"无法启动 cloudflared：{exc}") from exc
            self._reader_task = asyncio.create_task(self._read_output(self._process))
            try:
                await asyncio.wait_for(self._ready.wait(), timeout=max(5.0, timeout))
            except asyncio.TimeoutError as exc:
                self.error = "等待 Cloudflare 分配公网地址超时"
                await self._stop_locked()
                raise RuntimeError(self.error) from exc
            if not self.url:
                message = self.error or "cloudflared 未返回临时公网地址"
                await self._stop_locked()
                raise RuntimeError(message)
            self._probe_task = asyncio.create_task(
                self._wait_until_reachable(self.url, self._process)
            )
            return self.url

    async def stop(self) -> None:
        async with self._lock:
            await self._stop_locked()

    async def _stop_locked(self) -> None:
        process = self._process
        task = self._reader_task
        probe_task = self._probe_task
        self._process = None
        self._reader_task = None
        self._probe_task = None
        self.url = ""
        self.started_at = 0.0
        self._reachable = False
        if process is not None and process.returncode is None:
            try:
                process.terminate()
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except (ProcessLookupError, asyncio.TimeoutError):
                if process.returncode is None:
                    process.kill()
                    await process.wait()
        if isinstance(task, asyncio.Task) and task is not asyncio.current_task() and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if (
            isinstance(probe_task, asyncio.Task)
            and probe_task is not asyncio.current_task()
            and not probe_task.done()
        ):
            probe_task.cancel()
            await asyncio.gather(probe_task, return_exceptions=True)

    async def _wait_until_reachable(
        self,
        public_url: str,
        process: asyncio.subprocess.Process | None,
        *,
        timeout: float = 45.0,
    ) -> None:
        """后台确认 Cloudflare 边缘地址已生效；失败时保留通道供客户端继续尝试。"""
        deadline = asyncio.get_running_loop().time() + max(1.0, timeout)
        health_url = f"{public_url.rstrip('/')}/health"
        try:
            while (
                self._process is process
                and process is not None
                and process.returncode is None
                and self.url == public_url
            ):
                try:
                    client_timeout = aiohttp.ClientTimeout(total=4.0)
                    async with aiohttp.ClientSession(timeout=client_timeout) as session:
                        async with session.get(health_url, allow_redirects=False) as response:
                            if response.status == 200:
                                self._reachable = True
                                self.error = ""
                                return
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    pass
                elapsed_deadline = asyncio.get_running_loop().time() >= deadline
                if elapsed_deadline:
                    self.error = "临时地址仍在生效中，可稍后直接重试访问"
                await asyncio.sleep(5.0 if elapsed_deadline else 1.5)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.error = f"公网地址连通性检查失败：{exc}"

    async def _read_output(self, process: asyncio.subprocess.Process) -> None:
        recent: list[str] = []
        try:
            stream = process.stdout
            if stream is None:
                self.error = "无法读取 cloudflared 输出"
                return
            while True:
                raw = await stream.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                if line:
                    recent.append(line)
                    recent = recent[-8:]
                match = QUICK_TUNNEL_URL.search(line)
                if match and not self.url:
                    self.url = match.group(0).rstrip("/")
                    self.started_at = time.time()
                    self._ready.set()
            await process.wait()
            if not self.url and not self.error:
                detail = next((line for line in reversed(recent) if "ERR" in line.upper()), "")
                self.error = detail[-300:] if detail else f"cloudflared 已退出（code={process.returncode}）"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.error = f"读取 cloudflared 状态失败：{exc}"
        finally:
            self._ready.set()
            if self._process is process and process.returncode is not None:
                self.url = ""
                self.started_at = 0.0
