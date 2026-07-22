# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import mimetypes
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from astrbot.api import logger

from .models import RoomSession

try:
    from aiohttp import ClientConnectionError, ClientSession, ClientTimeout, WSMsgType, web
except ImportError:  # pragma: no cover - reported clearly during plugin startup
    ClientConnectionError = ConnectionError
    ClientSession = None
    ClientTimeout = None
    WSMsgType = None
    web = None


class TogetherRoomServer:
    MAX_WEBSOCKET_MESSAGE_BYTES = 16 * 1024 * 1024
    def __init__(self, plugin: Any, *, host: str, port: int, web_root: Path) -> None:
        self.plugin = plugin
        self.host = str(host or "127.0.0.1").strip() or "127.0.0.1"
        self.requested_port = max(1, min(int(port or 6321), 65535))
        self.port = self.requested_port
        self.web_root = Path(web_root)
        self._runner = None
        self._site = None
        self._proxy_session = None

    @property
    def running(self) -> bool:
        return self._runner is not None and self._site is not None

    @property
    def local_base_url(self) -> str:
        host = self.host
        if host in {"0.0.0.0", "::", "[::]"}:
            host = "127.0.0.1"
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"http://{host}:{self.port}"

    async def start(self) -> None:
        if self.running:
            return
        if web is None:
            raise RuntimeError("缺少 aiohttp，无法启动实时房间服务")

        app = web.Application(client_max_size=self.MAX_WEBSOCKET_MESSAGE_BYTES)
        app.router.add_get("/", self._serve_index)
        app.router.add_get("/join/{ticket}", self._serve_index)
        app.router.add_get("/assets/{name}", self._serve_asset)
        app.router.add_get("/avatar", self._serve_avatar)
        app.router.add_get("/media/{token}/{track}", self._serve_media)
        app.router.add_get("/media/{token}", self._serve_media)
        app.router.add_get("/health", self._serve_health)
        app.router.add_get("/ws", self._serve_websocket)

        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        last_error: Exception | None = None
        for candidate in range(self.requested_port, min(65535, self.requested_port + 10) + 1):
            site = web.TCPSite(self._runner, self.host, candidate)
            try:
                await site.start()
            except OSError as exc:
                last_error = exc
                continue
            self._site = site
            self.port = candidate
            if candidate != self.requested_port:
                logger.warning(
                    "[TogetherCompanion] 房间端口 %s 被占用，已改用 %s",
                    self.requested_port,
                    candidate,
                )
            logger.info("[TogetherCompanion] 实时房间已启动: %s", self.local_base_url)
            return

        await self.stop()
        raise RuntimeError(f"无法监听房间端口 {self.requested_port}-{self.requested_port + 10}: {last_error}")

    async def _media_session(self):
        """媒体转发共享会话：复用连接，避免每个 Range 请求新建 ClientSession。"""
        if self._proxy_session is None or self._proxy_session.closed:
            timeout = ClientTimeout(total=None, connect=15, sock_connect=15, sock_read=90)
            self._proxy_session = ClientSession(timeout=timeout)
        return self._proxy_session

    async def stop(self) -> None:
        site, runner = self._site, self._runner
        self._site = None
        self._runner = None
        if self._proxy_session is not None and not self._proxy_session.closed:
            try:
                await self._proxy_session.close()
            except Exception as exc:
                logger.debug("[TogetherCompanion] 关闭媒体转发会话失败: %s", exc)
        self._proxy_session = None
        if site is not None:
            try:
                await site.stop()
            except Exception as exc:
                logger.debug("[TogetherCompanion] 停止房间站点失败: %s", exc)
        if runner is not None:
            try:
                await runner.cleanup()
            except Exception as exc:
                logger.debug("[TogetherCompanion] 清理房间服务失败: %s", exc)

    @staticmethod
    def _security_headers(content_type: str = "") -> dict[str, str]:
        headers = {
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Permissions-Policy": "camera=(self), microphone=(self), geolocation=()",
        }
        if content_type.startswith("text/html"):
            headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; "
                "style-src 'self'; img-src 'self' data: blob:; "
                "media-src 'self' data: blob: https: http:; "
                "connect-src 'self' ws: wss:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
            )
        return headers

    async def _serve_index(self, request):
        path = self.web_root / "index.html"
        if not path.is_file():
            raise web.HTTPNotFound(text="房间页面不存在")
        return web.FileResponse(
            path,
            headers=self._security_headers("text/html; charset=utf-8"),
        )

    async def _serve_asset(self, request):
        allowed = {"app.css", "app.js", "lucide.min.js"}
        name = str(request.match_info.get("name") or "")
        if name not in allowed:
            raise web.HTTPNotFound()
        path = self.web_root / name
        if not path.is_file():
            raise web.HTTPNotFound()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return web.FileResponse(path, headers=self._security_headers(content_type))

    async def _serve_avatar(self, request):
        path = await self.plugin.resolve_avatar_path()
        if path is None or not path.is_file():
            raise web.HTTPNotFound()
        content_type = await asyncio.to_thread(self._avatar_content_type, path)
        return web.FileResponse(path, headers=self._security_headers(content_type))

    @staticmethod
    def _avatar_content_type(path: Path) -> str:
        try:
            with path.open("rb") as stream:
                header = stream.read(12)
            if header.startswith(b"\xff\xd8\xff"):
                return "image/jpeg"
            if header.startswith(b"\x89PNG\r\n\x1a\n"):
                return "image/png"
            if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
                return "image/webp"
        except OSError:
            pass
        return mimetypes.guess_type(path.name)[0] or "image/png"

    async def _serve_media(self, request):
        token = str(request.match_info.get("token") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{24,80}", token):
            raise web.HTTPNotFound(text="视频地址无效")
        source = self.plugin.resolve_media_source(token)
        if source is None:
            raise web.HTTPNotFound(text="视频地址已失效，请重新打开链接")
        if ClientSession is None or ClientTimeout is None:
            raise web.HTTPServiceUnavailable(text="媒体转发服务不可用")

        track = str(request.match_info.get("track") or "video").lower()
        if track not in {"video", "audio"}:
            raise web.HTTPNotFound(text="媒体轨道无效")
        source_url = source.source_url if track == "video" else source.audio_source_url
        content_type = source.content_type if track == "video" else source.audio_content_type
        if not source_url:
            raise web.HTTPNotFound(text="媒体轨道不存在")

        upstream_headers = dict(source.request_headers)
        for name in ("Range", "If-Range"):
            value = request.headers.get(name)
            if value:
                upstream_headers[name] = value
        session = await self._media_session()
        async with session.request(
            request.method,
            source_url,
            headers=upstream_headers,
            allow_redirects=True,
            max_redirects=5,
        ) as upstream:
            if upstream.status not in {200, 206}:
                raise web.HTTPBadGateway(text=f"视频源暂时不可用（HTTP {upstream.status}）")
            response_headers = self._security_headers(content_type)
            for name in (
                "Content-Length",
                "Content-Range",
                "Accept-Ranges",
                "ETag",
                "Last-Modified",
            ):
                value = upstream.headers.get(name)
                if value:
                    response_headers[name] = value
            response_headers.setdefault("Content-Type", content_type)
            response_headers["Content-Disposition"] = "inline"
            response = web.StreamResponse(status=upstream.status, headers=response_headers)
            try:
                await response.prepare(request)
            except (ConnectionError, ClientConnectionError):
                return response
            if request.method == "HEAD":
                return response
            try:
                async for chunk in upstream.content.iter_chunked(256 * 1024):
                    await response.write(chunk)
                await response.write_eof()
            except (ConnectionError, ClientConnectionError):
                pass
            return response

    async def _serve_health(self, request):
        return web.json_response(
            {
                "ok": True,
                "plugin": "astrbot_plugin_together_companion",
                "port": self.port,
                "rooms": len(self.plugin.rooms),
            },
            headers=self._security_headers("application/json"),
        )

    def _origin_allowed(self, request) -> bool:
        origin = str(request.headers.get("Origin") or "").strip()
        if not origin:
            return False
        try:
            parsed = urlsplit(origin)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                return False
            origin_value = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
            request_value = f"{str(request.scheme or 'http').lower()}://{str(request.host or '').lower()}"
        except Exception:
            return False
        if origin_value == request_value:
            return True
        quick_tunnel = getattr(self.plugin, "quick_tunnel", None)
        allowed_bases = (
            str(getattr(self.plugin, "public_base_url", "") or "").strip(),
            str(getattr(quick_tunnel, "url", "") or "").strip()
            if bool(getattr(quick_tunnel, "running", False))
            else "",
        )
        for public_base in allowed_bases:
            if not public_base:
                continue
            try:
                public = urlsplit(public_base)
                public_origin = f"{public.scheme.lower()}://{public.netloc.lower()}"
                if public.scheme and public.netloc and origin_value == public_origin:
                    return True
            except Exception:
                continue
        return False

    async def _serve_websocket(self, request):
        if not self._origin_allowed(request):
            raise web.HTTPForbidden(text="房间来源校验失败")

        resume_token = str(request.query.get("resume") or "").strip()
        token = str(request.query.get("ticket") or "").strip()
        resuming = bool(resume_token) and self.plugin.can_resume_room(resume_token)
        if not resuming:
            ticket = self.plugin.ticket_store.get(token)
            if ticket is None:
                raise web.HTTPUnauthorized(text="房间链接无效或已过期")

        websocket = web.WebSocketResponse(
            heartbeat=20,
            receive_timeout=180,
            max_msg_size=self.MAX_WEBSOCKET_MESSAGE_BYTES,
            autoping=True,
        )
        await websocket.prepare(request)
        resumed = False
        if resuming:
            room = await self.plugin.resume_room(resume_token, websocket)
            if room is None:
                await websocket.close(code=1008, message="房间已结束".encode("utf-8"))
                return websocket
            resumed = True
        else:
            ticket = self.plugin.ticket_store.consume(token)
            if ticket is None:
                await websocket.close(code=1008, message="房间链接已被使用".encode("utf-8"))
                return websocket
            room = await self.plugin.open_room(ticket, websocket)
        try:
            await self.plugin.send_room_payload(
                room,
                {
                    "type": "ready",
                    "room": await self.plugin.room_bootstrap(room),
                    "resumed": resumed,
                    "resume_token": room.resume_token,
                },
            )
            if resumed:
                await self.plugin.replay_room_state(room)
            async for message in websocket:
                if message.type == WSMsgType.TEXT:
                    try:
                        payload = json.loads(message.data)
                    except json.JSONDecodeError:
                        await self.plugin.send_room_error(room, "收到的房间消息不是有效 JSON")
                        continue
                    if not isinstance(payload, dict):
                        await self.plugin.send_room_error(room, "房间消息格式无效")
                        continue
                    await self.plugin.handle_room_payload(room, payload)
                elif message.type in {WSMsgType.ERROR, WSMsgType.CLOSE, WSMsgType.CLOSED}:
                    break
        except asyncio.TimeoutError:
            await websocket.close(code=1001, message="房间长时间无活动".encode("utf-8"))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("[TogetherCompanion] 房间连接异常: %s", exc, exc_info=True)
        finally:
            await self.plugin.detach_room(room)
        return websocket
