"""Official-only credential lifecycle; no SDK or provider result formatting."""

import asyncio
import io
from pathlib import Path
import time

from bilibili_api import Credential
from bilibili_api.user import get_self_info
import qrcode

from .storage import read_json, write_json, cookie_dict
from .transport import request_json, UA, IOS_UA, ProviderError


class CredentialService:
    def __init__(self, directory, settings, client):
        self.directory, self.settings, self.client = Path(directory), settings, client
        self.credential_file = self.directory / "credential.json"
        self.visitor_file = self.directory / "douyin-visitor.json"
        self.lock = asyncio.Lock()
        self.qr_key = ""
        self.cached, self.checked = None, 0

    def invalidate(self, settings):
        self.settings = settings
        self.cached, self.checked = None, 0

    def configured_bili_cookies(self):
        cfg = self.settings
        return {
            "SESSDATA": cfg.sessdata,
            "bili_jct": cfg.bili_jct,
            "buvid3": cfg.buvid3,
            "DedeUserID": cfg.dedeuserid,
        }

    async def bilibili(self):
        async with self.lock:
            if self.checked and time.monotonic() - self.checked < 1800:
                return self.cached
            saved = read_json(self.credential_file)
            self.cached = None
            for cookies in [saved, self.configured_bili_cookies()]:
                if not cookies.get("SESSDATA"):
                    continue
                try:
                    credential = Credential.from_cookies(cookies)
                    if await credential.check_valid():
                        self.cached = credential
                        break
                except Exception:
                    continue
            self.checked = time.monotonic()
            return self.cached

    async def qrcode(self):
        data, _ = await request_json(
            self.client, "GET", "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
        )
        detail = data.get("data") or {}
        if (
            data.get("code") != 0
            or not detail.get("qrcode_key")
            or not str(detail.get("url", "")).startswith("https://")
        ):
            raise ProviderError("未取得有效登录二维码")
        self.qr_key = str(detail["qrcode_key"])

        def draw():
            image = qrcode.make(detail["url"])
            out = io.BytesIO()
            image.save(out, format="PNG")
            return out.getvalue()

        return await asyncio.to_thread(draw)

    async def poll(self):
        key = self.qr_key
        if not key:
            return "请先生成登录二维码"
        try:
            for _ in range(90):
                data, cookies = await request_json(
                    self.client,
                    "GET",
                    "https://passport.bilibili.com/x/passport-login/web/qrcode/poll",
                    params={"qrcode_key": key},
                )
                code = (data.get("data") or {}).get("code", -1)
                if code == 0:
                    if not cookies.get("SESSDATA"):
                        return "登录未取得有效凭据"
                    keep = {"SESSDATA", "bili_jct", "buvid3", "DedeUserID", "ac_time_value"}
                    async with self.lock:
                        write_json(
                            self.credential_file, {k: v for k, v in cookies.items() if k in keep}
                        )
                        self.cached, self.checked = None, 0
                    return "B站登录成功，凭据已私有保存"
                if code == 86038:
                    return "二维码已过期，请重新登录"
                if code not in {86090, 86101}:
                    return "扫码登录未完成，请重新登录"
                await asyncio.sleep(2)
            return "登录超时，请重新登录"
        finally:
            if self.qr_key == key:
                self.qr_key = ""

    async def status(self):
        credential = await self.bilibili()
        if credential is None:
            return "B站未登录或凭据失效"
        try:
            profile = await get_self_info(credential)
            return "B站已登录：" + str(profile.get("name") or "未知用户")[:100]
        except Exception:
            return "B站凭据已配置，当前状态未确认"

    async def logout(self):
        async with self.lock:
            self.credential_file.unlink(missing_ok=True)
            self.cached, self.checked = None, 0
            self.qr_key = ""
        if self.settings.sessdata:
            return "扫码凭据已删除；配置中的Cookie仍存在，需在设置中清除"
        return "B站扫码凭据已清除"

    async def douyin_cookie(self):
        async with self.lock:
            cookies = read_json(self.visitor_file)
            cookies.update(cookie_dict(self.settings.douyin_cookie))
            if not cookies.get("ttwid"):
                try:
                    _, received = await request_json(
                        self.client,
                        "POST",
                        "https://ttwid.bytedance.com/ttwid/union/register/",
                        json={
                            "region": "cn",
                            "aid": 1768,
                            "needFid": False,
                            "service": "www.ixigua.com",
                            "migrate_info": {"ticket": "", "source": "node"},
                            "cbUrlProtocol": "https",
                            "union": True,
                        },
                        headers={"User-Agent": IOS_UA},
                    )
                    if received.get("ttwid"):
                        cookies["ttwid"] = received["ttwid"]
                        write_json(self.visitor_file, {"ttwid": received["ttwid"]})
                except ProviderError:
                    pass
            return "; ".join(f"{k}={v}" for k, v in cookies.items() if isinstance(v, str))

    def headers(self, cookie):
        return {"User-Agent": UA, "Cookie": cookie, "Referer": "https://www.douyin.com/"}
