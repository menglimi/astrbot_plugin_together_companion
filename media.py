# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from aiohttp import ClientSession, ClientTimeout


BVID_PATTERN = re.compile(r"(?<![A-Za-z0-9])(BV[A-Za-z0-9]{10})(?![A-Za-z0-9])", re.IGNORECASE)
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)

BILIBILI_QUALITY_LABELS = {
    127: "8K",
    126: "杜比视界",
    125: "HDR",
    120: "4K",
    116: "1080P60",
    112: "1080P+",
    80: "1080P",
    74: "720P60",
    64: "720P",
    32: "480P",
    16: "360P",
}


def _host_matches(host: str, suffix: str) -> bool:
    normalized = str(host or "").strip().lower().rstrip(".")
    return normalized == suffix or normalized.endswith(f".{suffix}")


def is_bilibili_page_url(value: Any) -> bool:
    try:
        parsed = urlsplit(str(value or "").strip())
    except Exception:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    host = str(parsed.hostname or "").lower()
    return any(_host_matches(host, suffix) for suffix in ("bilibili.com", "b23.tv"))


@dataclass(slots=True)
class ResolvedMedia:
    token: str
    room_id: str
    source_url: str
    title: str
    page_url: str = ""
    bvid: str = ""
    uploader: str = ""
    category: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    duration: float = 0.0
    quality: int = 0
    quality_label: str = ""
    content_type: str = "video/mp4"
    playback_mode: str = "progressive"
    audio_source_url: str = ""
    audio_content_type: str = "audio/mp4"
    request_headers: dict[str, str] = field(default_factory=dict)
    subtitle_cues: list[dict[str, Any]] = field(default_factory=list)
    subtitle_language: str = ""
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0

    @property
    def expired(self) -> bool:
        return bool(self.expires_at and time.time() >= self.expires_at)


class BilibiliMediaResolver:
    def __init__(self, data_root: Path) -> None:
        self.data_root = Path(data_root)

    async def resolve(
        self,
        page_url: str,
        *,
        room_id: str,
        request_headers: dict[str, str] | None = None,
    ) -> ResolvedMedia:
        normalized = str(page_url or "").strip()[:2000]
        if not is_bilibili_page_url(normalized):
            raise ValueError("这不是可识别的 B 站视频链接")

        timeout = ClientTimeout(total=30, connect=10, sock_read=20)
        headers = self._api_headers(request_headers)
        async with ClientSession(timeout=timeout, headers=headers) as session:
            normalized = await self._expand_short_url(session, normalized)
            bvid = self._extract_bvid(normalized)
            if not bvid:
                raise ValueError("没有从链接中找到 BV 号")

            view = await self._get_json(
                session,
                "https://api.bilibili.com/x/web-interface/view",
                params={"bvid": bvid},
            )
            view_data = self._payload_data(view, "读取视频信息失败")
            page = self._select_page(view_data, normalized)
            cid = int(page.get("cid") or view_data.get("cid") or 0)
            if cid <= 0:
                raise RuntimeError("B 站没有返回可播放的分 P 信息")

            audio_stream: dict[str, Any] | None = None
            playback_mode = "progressive"
            try:
                play_data, stream, audio_stream = await self._fetch_dash_stream(session, bvid, cid)
                playback_mode = "dash"
            except Exception:
                play_data, stream = await self._fetch_progressive_stream(session, bvid, cid)

            source_url = str(stream.get("url") or "").strip()
            source = urlsplit(source_url)
            if source.scheme not in {"http", "https"} or not source.hostname:
                raise RuntimeError("B 站返回了无效的媒体地址")

            title = str(view_data.get("title") or bvid).strip()[:180]
            part_title = str(page.get("part") or "").strip()
            pages = view_data.get("pages") if isinstance(view_data.get("pages"), list) else []
            if len(pages) > 1 and part_title:
                title = f"{title} - {part_title}"[:180]
            duration = float(page.get("duration") or view_data.get("duration") or 0.0)
            duration = max(0.0, duration)
            quality = max(0, int(play_data.get("quality") or 0))
            quality_label = BILIBILI_QUALITY_LABELS.get(quality, f"清晰度 {quality}" if quality else "")
            media_ttl_seconds = min(12 * 60 * 60, max(3 * 60 * 60, duration + 90 * 60))
            owner = view_data.get("owner") if isinstance(view_data.get("owner"), dict) else {}
            tags = await self._fetch_tags(session, bvid)
            subtitle_cues, subtitle_language = await self._fetch_subtitles(session, bvid, cid)
            return ResolvedMedia(
                token=secrets.token_urlsafe(24),
                room_id=str(room_id or ""),
                source_url=source_url,
                title=title,
                page_url=normalized,
                bvid=bvid,
                uploader=re.sub(r"\s+", " ", str(owner.get("name") or "")).strip()[:100],
                category=re.sub(r"\s+", " ", str(view_data.get("tname") or "")).strip()[:80],
                description=re.sub(r"\s+", " ", str(view_data.get("desc") or "")).strip()[:2000],
                tags=tags,
                duration=duration,
                quality=quality,
                quality_label=quality_label,
                playback_mode=playback_mode,
                audio_source_url=str((audio_stream or {}).get("url") or "").strip(),
                # The signed CDN URL normally does not need the account Cookie.
                # Keep only browser-like headers for the media proxy request.
                request_headers={
                    "User-Agent": headers.get("User-Agent", DEFAULT_USER_AGENT),
                    "Referer": headers.get("Referer", "https://www.bilibili.com/"),
                },
                subtitle_cues=subtitle_cues,
                subtitle_language=subtitle_language,
                expires_at=time.time() + media_ttl_seconds,
            )

    async def _fetch_dash_stream(
        self,
        session: ClientSession,
        bvid: str,
        cid: int,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        play = await self._get_json(
            session,
            "https://api.bilibili.com/x/player/playurl",
            params={
                "bvid": bvid,
                "cid": str(cid),
                "qn": "127",
                "fnver": "0",
                "fnval": "4048",
                "fourk": "1",
                "try_look": "1",
            },
        )
        play_data = self._payload_data(play, "读取高清播放地址失败")
        dash = play_data.get("dash") if isinstance(play_data.get("dash"), dict) else {}
        videos = [self._normalize_dash_stream(item) for item in (dash.get("video") or [])]
        audios = [self._normalize_dash_stream(item) for item in (dash.get("audio") or [])]
        videos = [item for item in videos if item and str(item.get("mime_type") or "").startswith("video/mp4")]
        audios = [item for item in audios if item and str(item.get("mime_type") or "").startswith("audio/mp4")]
        compatible_videos = [item for item in videos if str(item.get("codecs") or "").lower().startswith("avc1")]
        compatible_audios = [item for item in audios if str(item.get("codecs") or "").lower().startswith("mp4a")]
        video = max(compatible_videos or videos, key=self._dash_video_sort_key, default=None)
        audio = max(compatible_audios or audios, key=lambda item: int(item.get("bandwidth") or 0), default=None)
        if video is None or audio is None:
            raise RuntimeError("B 站没有返回浏览器兼容的高清音视频轨道")
        selected = dict(play_data)
        selected["quality"] = int(video.get("id") or play_data.get("quality") or 0)
        return selected, video, audio

    @staticmethod
    def _normalize_dash_stream(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        url = str(value.get("baseUrl") or value.get("base_url") or "").strip()
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return {}
        return {
            "url": url,
            "id": int(value.get("id") or 0),
            "bandwidth": int(value.get("bandwidth") or 0),
            "codecs": str(value.get("codecs") or "").strip(),
            "mime_type": str(value.get("mimeType") or value.get("mime_type") or "").strip(),
        }

    @staticmethod
    def _dash_video_sort_key(value: dict[str, Any]) -> tuple[int, int]:
        return int(value.get("id") or 0), int(value.get("bandwidth") or 0)

    async def _fetch_progressive_stream(
        self,
        session: ClientSession,
        bvid: str,
        cid: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        last_error: Exception | None = None
        for requested_quality in ("127", "80"):
            try:
                play = await self._get_json(
                    session,
                    "https://api.bilibili.com/x/player/playurl",
                    params={
                        "bvid": bvid,
                        "cid": str(cid),
                        "qn": requested_quality,
                        "fnver": "0",
                        "fnval": "0",
                        "fourk": "1",
                        "platform": "pc",
                    },
                )
                play_data = self._payload_data(play, "读取播放地址失败")
            except Exception as exc:
                last_error = exc
                continue
            streams = play_data.get("durl") if isinstance(play_data.get("durl"), list) else []
            stream = next((item for item in streams if isinstance(item, dict) and item.get("url")), None)
            if stream is not None:
                return play_data, stream
            last_error = RuntimeError("B 站没有返回可直接播放的合并流")
        if last_error is not None:
            raise last_error
        raise RuntimeError("该视频只提供音视频分离流，当前版本暂时无法直接播放")

    async def _fetch_tags(self, session: ClientSession, bvid: str) -> list[str]:
        try:
            payload = await self._get_json(
                session,
                "https://api.bilibili.com/x/tag/archive/tags",
                params={"bvid": bvid},
            )
            if int(payload.get("code") or 0) != 0:
                return []
            data = payload.get("data") if isinstance(payload.get("data"), list) else []
            tags: list[str] = []
            for item in data[:16]:
                if not isinstance(item, dict):
                    continue
                name = re.sub(r"\s+", " ", str(item.get("tag_name") or "")).strip()[:40]
                if name and name not in tags:
                    tags.append(name)
            return tags
        except Exception:
            return []

    async def _fetch_subtitles(
        self,
        session: ClientSession,
        bvid: str,
        cid: int,
    ) -> tuple[list[dict[str, Any]], str]:
        try:
            player = await self._get_json(
                session,
                "https://api.bilibili.com/x/player/v2",
                params={"bvid": bvid, "cid": str(cid)},
            )
            player_data = self._payload_data(player, "读取字幕信息失败")
            subtitle = player_data.get("subtitle") if isinstance(player_data.get("subtitle"), dict) else {}
            tracks = [item for item in (subtitle.get("subtitles") or []) if isinstance(item, dict)]
            if not tracks:
                return [], ""
            preferred = ("zh-CN", "zh-Hans", "ai-zh", "zh-Hant", "zh-TW")
            track = next(
                (item for language in preferred for item in tracks if str(item.get("lan") or "") == language),
                tracks[0],
            )
            subtitle_url = str(track.get("subtitle_url") or track.get("url") or "").strip()
            if subtitle_url.startswith("//"):
                subtitle_url = f"https:{subtitle_url}"
            parsed = urlsplit(subtitle_url)
            if parsed.scheme != "https" or not parsed.hostname:
                return [], ""
            payload = await self._get_json(session, subtitle_url, params={})
            body = payload.get("body") if isinstance(payload.get("body"), list) else []
            cues: list[dict[str, Any]] = []
            for item in body[:12000]:
                if not isinstance(item, dict):
                    continue
                try:
                    start = max(0.0, float(item.get("from") or 0.0))
                    end = max(start, float(item.get("to") or start))
                except (TypeError, ValueError):
                    continue
                content = re.sub(r"\s+", " ", str(item.get("content") or "")).strip()[:300]
                if content:
                    cues.append({"start": round(start, 3), "end": round(end, 3), "text": content})
            return cues, str(track.get("lan_doc") or track.get("lan") or "")[:40]
        except Exception:
            return [], ""

    async def _expand_short_url(self, session: ClientSession, url: str) -> str:
        parsed = urlsplit(url)
        if not _host_matches(parsed.hostname or "", "b23.tv"):
            return url
        async with session.get(url, allow_redirects=True, max_redirects=5) as response:
            final_url = str(response.url)
        if not _host_matches(urlsplit(final_url).hostname or "", "bilibili.com"):
            raise ValueError("B 站短链接跳转到了不受支持的站点")
        return final_url

    @staticmethod
    def _extract_bvid(url: str) -> str:
        match = BVID_PATTERN.search(str(url or ""))
        if not match:
            return ""
        value = match.group(1)
        return f"BV{value[2:]}"

    @staticmethod
    def _select_page(view_data: dict[str, Any], page_url: str) -> dict[str, Any]:
        pages = [item for item in (view_data.get("pages") or []) if isinstance(item, dict)]
        if not pages:
            return {}
        try:
            page_number = int((parse_qs(urlsplit(page_url).query).get("p") or ["1"])[0])
        except (TypeError, ValueError):
            page_number = 1
        page_number = max(1, page_number)
        return next((item for item in pages if int(item.get("page") or 0) == page_number), pages[0])

    async def _get_json(self, session: ClientSession, url: str, *, params: dict[str, str]) -> dict[str, Any]:
        async with session.get(url, params=params) as response:
            if response.status != 200:
                raise RuntimeError(f"B 站接口返回 HTTP {response.status}")
            payload = await response.json(content_type=None)
        if not isinstance(payload, dict):
            raise RuntimeError("B 站接口返回格式异常")
        return payload

    @staticmethod
    def _payload_data(payload: dict[str, Any], fallback: str) -> dict[str, Any]:
        code = int(payload.get("code") or 0)
        data = payload.get("data")
        if code != 0 or not isinstance(data, dict):
            message = str(payload.get("message") or fallback).strip()
            raise RuntimeError(message[:240])
        return data

    def _api_headers(self, overrides: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Referer": "https://www.bilibili.com/",
            "Accept": "application/json, text/plain, */*",
        }
        cookie = self._bilibili_cookie()
        if cookie:
            headers["Cookie"] = cookie
        if isinstance(overrides, dict):
            canonical_keys = {
                "cookie": "Cookie",
                "user-agent": "User-Agent",
                "referer": "Referer",
                "accept": "Accept",
                "accept-encoding": "Accept-Encoding",
            }
            for key, value in overrides.items():
                normalized_key = str(key or "").strip()
                canonical_key = canonical_keys.get(normalized_key.lower())
                if canonical_key:
                    text = re.sub(r"[\r\n]", "", str(value or "")).strip()
                    if text:
                        headers[canonical_key] = text
        return headers

    def _bilibili_cookie(self) -> str:
        path = self.data_root / "config" / "astrbot_plugin_bilibili_bot_config.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return ""
        if not isinstance(data, dict):
            return ""
        pairs = []
        for config_key, cookie_key in (
            ("SESSDATA", "SESSDATA"),
            ("BILI_JCT", "bili_jct"),
            ("DEDE_USER_ID", "DedeUserID"),
            ("BUVID3", "buvid3"),
            ("BUVID4", "buvid4"),
        ):
            value = re.sub(r"[\r\n;]", "", str(data.get(config_key) or "").strip())
            if value:
                pairs.append(f"{cookie_key}={value}")
        return "; ".join(pairs)
