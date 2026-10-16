"""Fixed official endpoints. Account cookies are never shared with parsers/media."""

import json
from urllib.parse import urlsplit

import httpx

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"
IOS_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 Version/16.6 Mobile/15E148 Safari/604.1"
OFFICIAL_HOSTS = {
    "www.douyin.com",
    "m.douyin.com",
    "www.iesdouyin.com",
    "so-landing.douyin.com",
    "ttwid.bytedance.com",
    "passport.bilibili.com",
}


class ProviderError(ValueError):
    pass


async def request_bytes(client, method, url, *, limit=2 * 1024 * 1024, **kwargs):
    # Callers construct these URLs, but keep a central credential boundary.
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in OFFICIAL_HOSTS:
        raise ProviderError("不允许的官方接口")
    try:
        async with client.stream(method, url, **kwargs) as response:
            if response.status_code != 200:
                raise ProviderError("官方接口暂不可用")
            data = bytearray()
            async for block in response.aiter_bytes():
                if len(data) + len(block) > limit:
                    raise ProviderError("官方响应超过限制")
                data.extend(block)
            return bytes(data), dict(response.cookies)
    except httpx.HTTPError:
        raise ProviderError("官方接口请求失败") from None


async def request_json(client, method, url, **kwargs):
    raw, cookies = await request_bytes(client, method, url, **kwargs)
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        raise ProviderError("官方接口响应无效") from None
    if not isinstance(value, dict):
        raise ProviderError("官方接口响应无效")
    return value, cookies
