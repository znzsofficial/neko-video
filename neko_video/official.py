"""Official video metadata/summary orchestration, independent of the SDK."""

import asyncio
import re
import secrets
from urllib.parse import urljoin, urlsplit

from bilibili_api.video import Video

from .cache import TTLCache
from .media import share_target, MediaError
from .parsers import basic_douyin, chapter_douyin, generation_douyin, stream_douyin
from .transport import request_bytes, request_json, ProviderError, UA, IOS_UA

BILI_HOSTS = {
    "b23.tv",
    "www.b23.tv",
    "bili2233.cn",
    "www.bili2233.cn",
    "bilibili.com",
    "www.bilibili.com",
    "m.bilibili.com",
}


class OfficialVideoService:
    def __init__(self, settings, credentials, client):
        self.settings, self.credentials, self.client = settings, credentials, client
        self.cache = TTLCache()
        self.slots = asyncio.Semaphore(2)

    async def resolve(self, text, expected=None):
        if not isinstance(text, str) or len(text) > 10000:
            return None
        # A bare ID is useful for an explicit tool but is never auto-detected as Douyin.
        if expected == "douyin" and re.fullmatch(r"\d{19}", text.strip()):
            return "douyin", text.strip()
        match = re.search(r"\b(BV[0-9A-Za-z]{10}|av\d{6,})\b", text, re.I)
        if match and expected != "douyin":
            token = match[1]
            return ("bvid", token) if token.upper().startswith("BV") else ("aid", int(token[2:]))
        for url in re.findall(r'https?://[^\s<>"“”]+', text):
            url = url.rstrip("，。！!）)；;、")
            p = urlsplit(url)
            if (
                p.hostname in BILI_HOSTS
                and p.port in {None, 80, 443}
                and p.username is None
                and p.password is None
                and expected != "douyin"
            ):
                for _ in range(4):
                    # Parse only after the host/URL boundary above. Direct IDs need no GET.
                    match = re.search(r"/video/(BV[0-9A-Za-z]{10}|av\d+)", url, re.I)
                    if match:
                        value = match[1]
                        return (
                            ("bvid", value)
                            if value.upper().startswith("BV")
                            else ("aid", int(value[2:]))
                        )
                    async with self.client.stream(
                        "GET", url, follow_redirects=False, headers={"Cookie": ""}
                    ) as response:
                        status = response.status_code
                        location = response.headers.get("location", "")
                    if status not in {301, 302, 303, 307, 308}:
                        break
                    url = urljoin(url, location)
                    p = urlsplit(url)
                    if (
                        p.scheme not in {"http", "https"}
                        or p.hostname not in BILI_HOSTS
                        or p.port not in {None, 80, 443}
                        or p.username is not None
                        or p.password is not None
                    ):
                        break
        if expected != "bilibili":
            try:
                vid, url = share_target(text)
                if vid:
                    return "douyin", vid
                for _ in range(4):
                    async with self.client.stream(
                        "GET", url, follow_redirects=False, headers={"Cookie": ""}
                    ) as response:
                        status = response.status_code
                        candidate = (
                            urljoin(url, response.headers.get("location", ""))
                            if status in {301, 302, 303, 307, 308}
                            else str(response.url)
                        )
                    vid, url = share_target(candidate)
                    if vid:
                        return "douyin", vid
                    p = urlsplit(url)
                    match = re.search(r"(?:^|&)modal_id=(\d{19})(?:&|$)", p.query)
                    if match:
                        return "douyin", match[1]
                    if status not in {301, 302, 303, 307, 308}:
                        break
            except MediaError:
                pass
        return None

    async def summary(self, text, expected=None):
        async with self.slots:
            target = await self.resolve(text, expected)
            if target is None:
                return None
            kind, vid = target
            if kind == "douyin" and not self.settings.enable_douyin:
                return None
            key = f"{kind}:{vid}"
            cached = self.cache.get(key)
            if cached is not None:
                return cached
            result = await (
                self._douyin(str(vid)) if kind == "douyin" else self._bilibili(kind, vid)
            )
            if result:
                self.cache.put(key, result, self.settings.cache_ttl_seconds)
            return result

    async def _bilibili(self, kind, vid):
        video = Video(bvid=vid) if kind == "bvid" else Video(aid=vid)
        info = await video.get_info()
        result = {
            "platform": "bilibili",
            "title": str(info.get("title") or "")[:2000],
            "author": str((info.get("owner") or {}).get("name") or "未知")[:120],
            "duration": info.get("duration", 0),
            "description": str(info.get("desc") or "")[:200],
            "url": "https://www.bilibili.com/video/" + str(info.get("bvid") or vid),
            "summary": "",
        }
        if self.settings.enable_ai_summary:
            credential = await self.credentials.bilibili()
            if credential is not None:
                try:
                    authenticated = Video(bvid=info.get("bvid"), credential=credential)
                    data = await authenticated.get_ai_conclusion(cid=info.get("cid"))
                    data = data.get("data") if isinstance(data.get("data"), dict) else data
                    result["summary"] = str((data.get("model_result") or {}).get("summary") or "")[
                        :6000
                    ]
                except Exception:
                    pass  # Keep real metadata, never invent a summary.
        return result

    async def _douyin(self, vid):
        cookie = await self.credentials.douyin_cookie()
        headers = {"User-Agent": IOS_UA, "Referer": "https://www.douyin.com/", "Cookie": cookie}
        info = None
        # No implicit cross-host redirect can forward the caller's cookie.
        for url in [
            f"https://www.iesdouyin.com/share/video/{vid}",
            f"https://m.douyin.com/share/video/{vid}",
        ]:
            try:
                raw, _ = await request_bytes(
                    self.client, "GET", url, headers=headers, limit=4 * 1024 * 1024
                )
                info = basic_douyin(raw.decode("utf-8", errors="replace"))
                if info:
                    break
            except ProviderError:
                continue
        if info is None:
            return None
        info.update(
            platform="douyin",
            url=f"https://www.douyin.com/video/{vid}",
            summary="",
            chapters=[],
            highlights=[],
        )
        if not self.settings.enable_ai_summary:
            return info
        official_headers = {
            "User-Agent": UA,
            "Cookie": cookie,
            "Referer": "https://so-landing.douyin.com/search_ai_mobile/pc",
        }
        if cookie:
            try:
                data, _ = await request_json(
                    self.client,
                    "GET",
                    "https://so-landing.douyin.com/douyin/select/v1/ai/generation/get/",
                    params={"key": vid, "aid": 6383},
                    headers=official_headers,
                )
                info.update(generation_douyin(data))
            except ProviderError:
                pass

        async def chapters():
            raw, _ = await request_bytes(
                self.client,
                "GET",
                f"https://www.douyin.com/jingxuan?modal_id={vid}",
                headers=official_headers,
                limit=4 * 1024 * 1024,
            )
            return chapter_douyin(raw.decode("utf-8", errors="replace"))

        async def streamed():
            params = {
                "count": "5",
                "cursor": "0",
                "token": "search",
                "ai_page_type": "ai_chat",
                "search_channel": "aweme_ai_chat",
                "enable_ai_tab_new_framework": "1",
                "need_integration_card": "1",
                "ai_chat_message_use_lynx": "1",
                "version_code": "32.1.0",
                "enter_method": "offline_summary_card",
                "enter_from": "general_search",
                "search_type": "ai_chat_search",
                "aid": "6383",
                "device_id": str(7000000000000000000 + secrets.randbelow(10**18)),
                "keyword": "总结当前视频内容：" + info["title"][:500],
                "ai_search_enter_from_group_id": vid,
                "aweme_id": vid,
            }
            raw, _ = await request_bytes(
                self.client,
                "GET",
                "https://so-landing.douyin.com/douyin/select/v1/ai/stream/",
                params=params,
                headers={**official_headers, "Accept": "text/event-stream"},
                timeout=25,
            )
            return stream_douyin(raw)

        jobs = [asyncio.create_task(chapters())]
        if cookie and not info["summary"]:
            jobs.append(asyncio.create_task(streamed()))
        try:
            results = await asyncio.gather(*jobs, return_exceptions=True)
            if isinstance(results[0], dict):
                info.update(results[0])
            if len(results) > 1 and isinstance(results[1], str):
                info["summary"] = results[1]
        finally:
            for job in jobs:
                if not job.done():
                    job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)
        return info
