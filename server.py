# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from importlib import resources as importlib_resources
import hmac
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


KEY_PAGE_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<meta name="color-scheme" content="light dark" />
<title>我会和你在一起 · 房间密钥</title>
<style>
  :root { color-scheme: light dark; }
  body { margin: 0; min-height: 100vh; display: grid; place-items: center;
    font-family: system-ui, -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    background: #f4f5f0; color: #1d221d; }
  @media (prefers-color-scheme: dark) { body { background: #282c29; color: #ecf0ea; } }
  form { width: min(88vw, 360px); padding: 32px 28px; border-radius: 18px;
    background: rgba(127, 127, 127, 0.08); display: grid; gap: 14px; box-sizing: border-box; }
  h1 { font-size: 18px; margin: 0; }
  p { margin: 0; font-size: 13px; opacity: 0.72; line-height: 1.6; }
  p.error { opacity: 1; color: #c0392b; }
  input { padding: 12px 14px; border-radius: 12px; border: 1px solid rgba(127, 127, 127, 0.4);
    font-size: 15px; background: transparent; color: inherit; box-sizing: border-box; }
  input:focus { outline: none; border-color: #4a7856; }
  button { padding: 12px; border: 0; border-radius: 12px; font-size: 15px;
    background: #4a7856; color: #fff; cursor: pointer; }
  button:disabled { opacity: 0.6; cursor: default; }
</style>
</head>
<body>
<form>
  <h1>请输入房间访问密钥</h1>
  <p>这个房间地址需要密钥才能访问，浏览器验证通过后会记住，之后无需重复输入。</p>
  __HINT__
  <input id="key" type="password" autocomplete="current-password" placeholder="访问密钥" autofocus />
  <button id="enter" type="button">进入房间</button>
</form>
<script>
(() => {
  "use strict";
  const input = document.getElementById("key");
  const button = document.getElementById("enter");
  async function enter() {
    const value = input.value.trim();
    if (!value) { input.focus(); return; }
    button.disabled = true;
    try {
      const response = await fetch("/auth?key=" + encodeURIComponent(value));
      if (response.ok) { window.location.reload(); return; }
    } catch {
      showHint("网络异常，请稍后重试。");
      button.disabled = false;
      input.focus();
      return;
    }
    showHint("密钥不正确，请重新输入。");
    button.disabled = false;
    input.focus();
  }
  function showHint(text) {
    let node = document.querySelector("p.error");
    if (!node) {
      node = document.createElement("p");
      node.className = "error";
      button.before(node);
    }
    node.textContent = text;
  }
  button.addEventListener("click", enter);
  input.addEventListener("keydown", (event) => { if (event.key === "Enter") enter(); });
})();
</script>
</body>
</html>
"""


class TogetherRoomServer:
    MAX_WEBSOCKET_MESSAGE_BYTES = 16 * 1024 * 1024
    REQUIRED_WEB_ASSETS = ("index.html", "app.css", "app.js", "lucide.min.js")
    ACCESS_KEY_COOKIE = "together_key"
    ACCESS_KEY_COOKIE_MAX_AGE = 30 * 86400

    def __init__(
        self,
        plugin: Any,
        *,
        host: str,
        port: int,
        web_root: Path,
        resource_package: str = "",
    ) -> None:
        self.plugin = plugin
        self.host = str(host or "127.0.0.1").strip() or "127.0.0.1"
        self.requested_port = max(1, min(int(port or 6321), 65535))
        self.port = self.requested_port
        self.web_root = Path(web_root)
        self.resource_packages = tuple(
            dict.fromkeys(
                package
                for package in (
                    str(resource_package or "").strip(),
                    "astrbot_plugin_together_companion",
                    "data.plugins.astrbot_plugin_together_companion",
                )
                if package
            )
        )
        self._packaged_asset_cache: dict[str, bytes] = {}
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

    @property
    def access_token(self) -> str:
        """Room access key configured on the plugin; empty means no key gate."""
        return str(getattr(self.plugin, "access_token", "") or "").strip()

    def _request_access_key(self, request) -> str:
        key = str(request.query.get("key") or "").strip()
        if not key:
            key = str(request.cookies.get(self.ACCESS_KEY_COOKIE) or "").strip()
        return key

    def _access_allowed(self, request) -> bool:
        token = self.access_token
        if not token:
            return True
        return hmac.compare_digest(self._request_access_key(request), token)

    def _set_access_cookie(self, response) -> None:
        response.set_cookie(
            self.ACCESS_KEY_COOKIE,
            self.access_token,
            max_age=self.ACCESS_KEY_COOKIE_MAX_AGE,
            httponly=True,
            samesite="Lax",
            path="/",
        )

    def _key_page_response(self, *, invalid: bool = False) -> "web.Response":
        """Self-contained key prompt page; inlined CSS/JS so it works before any asset or cookie access."""
        hint = "<p class=\"error\">密钥不正确，请重新输入。</p>" if invalid else ""
        html = KEY_PAGE_HTML.replace("__HINT__", hint)
        headers = self._security_headers("text/html; charset=utf-8")
        headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
            "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'"
        )
        return web.Response(
            body=html,
            content_type="text/html",
            charset="utf-8",
            headers=headers,
        )

    async def start(self) -> None:
        if self.running:
            return
        if web is None:
            raise RuntimeError("缺少 aiohttp，无法启动实时房间服务")

        missing_assets = [
            name for name in self.REQUIRED_WEB_ASSETS if not self._web_asset_available(name)
        ]
        if missing_assets:
            raise RuntimeError(
                "房间静态资源不完整，缺少："
                + "、".join(missing_assets)
                + f"；已检查目录 {self.web_root} 和包资源 {', '.join(self.resource_packages)}"
            )

        app = web.Application(client_max_size=self.MAX_WEBSOCKET_MESSAGE_BYTES)
        app.router.add_get("/", self._serve_index)
        app.router.add_get("/join/{ticket}", self._serve_index)
        app.router.add_get("/auth", self._serve_auth)
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

    def _filesystem_web_asset(self, name: str) -> Path | None:
        filename = str(name or "")
        if filename not in self.REQUIRED_WEB_ASSETS:
            return None
        candidates = [self.web_root / filename]
        plugin_root = Path(getattr(self.plugin, "plugin_root", self.web_root.parent))
        candidates.extend(
            (
                plugin_root / "web" / filename,
                plugin_root / "astrbot_plugin_together_companion" / "web" / filename,
                plugin_root.parent / "astrbot_plugin_together_companion" / "web" / filename,
            )
        )
        for parent in (plugin_root, plugin_root.parent):
            try:
                candidates.extend(
                    path / filename
                    for path in parent.glob("astrbot_plugin_together_companion*/web")
                )
            except OSError:
                continue
        seen: set[str] = set()
        for candidate in candidates:
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            try:
                if candidate.is_file():
                    return candidate
            except OSError:
                continue
        return None

    def _packaged_web_asset(self, name: str) -> bytes | None:
        filename = str(name or "")
        if filename not in self.REQUIRED_WEB_ASSETS:
            return None
        cached = self._packaged_asset_cache.get(filename)
        if cached is not None:
            return cached
        for package in self.resource_packages:
            try:
                asset = importlib_resources.files(package).joinpath("web").joinpath(filename)
                if not asset.is_file():
                    continue
                content = asset.read_bytes()
            except (
                AttributeError,
                ImportError,
                ModuleNotFoundError,
                FileNotFoundError,
                OSError,
                TypeError,
            ):
                continue
            self._packaged_asset_cache[filename] = content
            return content
        return None

    def _web_asset_available(self, name: str) -> bool:
        return self._filesystem_web_asset(name) is not None or self._packaged_web_asset(name) is not None

    def _web_asset_diagnostic(self, name: str) -> str:
        packages = ", ".join(self.resource_packages) or "未配置"
        return f"资源={name} 文件目录={self.web_root} 包资源={packages}"

    async def _serve_index(self, request):
        if not self._access_allowed(request):
            provided_key = str(request.query.get("key") or "").strip()
            return self._key_page_response(invalid=bool(provided_key))
        response = await self._index_response()
        provided_key = str(request.query.get("key") or "").strip()
        if provided_key and hmac.compare_digest(provided_key, self.access_token):
            self._set_access_cookie(response)
        return response

    async def _index_response(self):
        path = self._filesystem_web_asset("index.html")
        if path is not None:
            return web.FileResponse(
                path,
                headers=self._security_headers("text/html; charset=utf-8"),
            )
        content = self._packaged_web_asset("index.html")
        if content is None:
            diagnostic = self._web_asset_diagnostic("index.html")
            logger.error("[TogetherCompanion] 房间页面资源缺失: %s", diagnostic)
            raise web.HTTPNotFound(text="房间页面不存在，静态资源不完整；请检查插件日志")
        return web.Response(
            body=content,
            content_type="text/html",
            charset="utf-8",
            headers=self._security_headers("text/html; charset=utf-8"),
        )

    async def _serve_auth(self, request):
        """Validate the access key from the key page and remember it via cookie."""
        provided_key = str(request.query.get("key") or "").strip()
        token = self.access_token
        if not token or not provided_key or not hmac.compare_digest(provided_key, token):
            raise web.HTTPUnauthorized(text="访问密钥不正确")
        response = web.json_response(
            {"ok": True},
            headers=self._security_headers("application/json"),
        )
        self._set_access_cookie(response)
        return response

    async def _serve_asset(self, request):
        if not self._access_allowed(request):
            raise web.HTTPUnauthorized(text="需要房间访问密钥")
        allowed = set(self.REQUIRED_WEB_ASSETS) - {"index.html"}
        name = str(request.match_info.get("name") or "")
        if name not in allowed:
            raise web.HTTPNotFound()
        path = self._filesystem_web_asset(name)
        if path is not None:
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            return web.FileResponse(path, headers=self._security_headers(content_type))
        content = self._packaged_web_asset(name)
        if content is None:
            logger.error(
                "[TogetherCompanion] 房间静态资源缺失: %s",
                self._web_asset_diagnostic(name),
            )
            raise web.HTTPNotFound()
        content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        return web.Response(
            body=content,
            content_type=content_type,
            headers=self._security_headers(content_type),
        )

    async def _serve_avatar(self, request):
        if not self._access_allowed(request):
            raise web.HTTPUnauthorized(text="需要房间访问密钥")
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
        if not self._access_allowed(request):
            raise web.HTTPUnauthorized(text="房间访问密钥缺失或不正确")

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
