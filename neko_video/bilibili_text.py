"""Safe Bilibili text errors and public, bounded subtitle excerpts; no SDK/models."""

import asyncio
from contextlib import aclosing
import json
import math
from urllib.parse import urlsplit

import httpx
from bilibili_api.exceptions import NetworkException, ResponseCodeException

from .media import MediaError, download_chunks

SUBTITLE_HOSTS = ("hdslb.com", "bilibili.com", "bilivideo.com", "biliapi.net")
MAX_SUBTITLE_BYTES = 2 * 1024 * 1024


def read_failure(error, stage):
    """Fixed diagnostics only: raw SDK errors can contain URLs/cookies/signatures."""
    code = None
    if isinstance(error, ResponseCodeException):
        code = error.code
    elif isinstance(error, NetworkException):
        code = error.status
    elif isinstance(error, httpx.HTTPStatusError):
        code = error.response.status_code
    suffix = f"（code={code}）" if type(code) is int else ""
    if code in {-101, -102, -111, -401, -403, 401, 403}:
        return "blocked", f"{stage}登录或权限校验未通过{suffix}，未继续请求其他内容接口"
    if code in {-352, -412, -509, -799, 412, 429}:
        return "blocked", f"{stage}被平台风控或限流拦截{suffix}，未继续请求其他内容接口"
    if code in {-504, 408, 504} or isinstance(error, (TimeoutError, httpx.TimeoutException)):
        return "temporary", f"{stage}服务调用超时{suffix}"
    if code in {-500, -503, 500, 502, 503} or (
        isinstance(error, httpx.TransportError) and not isinstance(error, httpx.HTTPStatusError)
    ):
        return "temporary", f"{stage}暂不可用或网络连接失败{suffix}"
    return "error", f"{stage}未取得有效结果{suffix}"


def subtitle_url(value):
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise MediaError("字幕地址无效")
    value = "https:" + value if value.startswith("//") else value
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        if (
            any(ord(c) < 33 for c in value)
            or parsed.scheme != "https"
            or parsed.port not in {None, 443}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or not any(host == h or host.endswith("." + h) for h in SUBTITLE_HOSTS)
        ):
            raise ValueError
        httpx.URL(value)
    except (ValueError, httpx.InvalidURL):
        raise MediaError("字幕地址不是允许的官方HTTPS地址") from None
    return value


def subtitle_excerpt(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("body"), list):
        raise MediaError("字幕响应格式无效")
    rows = []
    for row in payload["body"][:10000]:
        if not isinstance(row, dict) or not isinstance(row.get("content"), str):
            continue
        try:
            start, end = float(row["from"]), float(row["to"])
        except (KeyError, ValueError, TypeError, OverflowError):
            continue
        text = row["content"].strip()[:1000]
        if text and math.isfinite(start) and math.isfinite(end) and 0 <= start <= end <= 604800:
            rows.append((start, end, text))
    rows.sort(key=lambda r: (r[0], r[1]))
    if not rows:
        return ""
    count = min(24, len(rows))
    positions = (
        [0] if count == 1 else [round(i * (len(rows) - 1) / (count - 1)) for i in range(count)]
    )
    per_line = min(1000, 4500 // count)
    selected = [rows[i] for i in positions]
    if sum(len(r[2][:per_line]) for r in selected) < 30:
        return ""
    return "\n".join(f"[{a:.1f}–{b:.1f}秒] {text[:per_line]}" for a, b, text in selected)


async def fetch_subtitle(value):
    url = subtitle_url(value)
    # Never reuse the official/account session or its cookie jar for a signed CDN URL.
    async with httpx.AsyncClient(
        timeout=3, follow_redirects=False, trust_env=False, headers={"User-Agent": "NekoVideo"}
    ) as client:
        raw = bytearray()
        async with asyncio.timeout(3):
            async with aclosing(download_chunks(client, url, allow_redirects=False)) as chunks:
                async for chunk in chunks:
                    if len(raw) + len(chunk) > MAX_SUBTITLE_BYTES:
                        raise MediaError("字幕文件超过2MiB限制")
                    raw.extend(chunk)
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError):
        raise MediaError("字幕文件格式无效") from None
    return subtitle_excerpt(payload)
