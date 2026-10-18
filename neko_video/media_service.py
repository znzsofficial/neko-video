"""Visual reads and single-media delivery. SDK-independent orchestration."""

import asyncio
import base64
import hashlib
from pathlib import Path

import httpx

from .assets import MediaAssets
from .media import MediaError, resolve_media, preview_result, download_image, validate_original
from .storage import read_json
from .transport import UA
from .video_spool import VideoSpool


class MediaService:
    def __init__(self, settings, context, *, assets=None):
        self.settings, self.context = settings, context
        self.assets = assets if assets is not None else MediaAssets()
        self.slots = asyncio.Semaphore(2)
        self.tasks = set()
        self.closed = False
        self.spool = (
            VideoSpool(Path(context.paths.data_dir)) if getattr(context, "paths", None) else None
        )
        self.housekeeper = None

    async def start(self):
        if self.spool is not None:
            await self.spool.clean()
            self.housekeeper = asyncio.create_task(self._housekeeping())

    async def _housekeeping(self):
        while True:
            await asyncio.sleep(300)
            try:
                await self.spool.clean()
            except OSError:
                pass  # Retain files rather than guessing paths or resending.

    def enabled(self):
        cfg = self.settings
        return (
            not self.closed and cfg.plugin.enabled and cfg.parse.enable_douyin and cfg.media.enabled
        )

    async def close(self):
        self.closed = True
        if self.housekeeper is not None:
            self.housekeeper.cancel()
            await asyncio.gather(self.housekeeper, return_exceptions=True)
            self.housekeeper = None
        tasks = [task for task in self.tasks if task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.assets.clear()

    def token(self):
        token = self.settings.media.fallback_token.strip()
        if token:
            return token
        path = Path(self.context.paths.data_dir) / "douyin-media-secrets.json"
        saved = read_json(path, limit=4096)
        if not isinstance(saved.get("fallback_token", ""), str):
            raise MediaError("媒体凭据格式错误")
        return saved.get("fallback_token", "").strip()

    async def read(self, link, start=0, scope=""):
        if not self.enabled():
            return {"success": False, "content": "抖音媒体读取未启用", "content_items": []}
        if isinstance(start, bool) or not isinstance(start, int) or not 0 <= start < 64:
            return {"success": False, "content": "图片起点应为0到63的整数", "content_items": []}
        task = asyncio.current_task()
        self.tasks.add(task)
        try:
            token = self.token()
            deadline = asyncio.get_running_loop().time() + 43
            async with asyncio.timeout(45):
                async with self.slots:
                    async with httpx.AsyncClient(
                        timeout=12, follow_redirects=False, trust_env=False
                    ) as api:
                        result = await resolve_media(api, link, token)
                    if start and start >= len(result["images"]):
                        raise MediaError("图片起点超过范围")
                    async with httpx.AsyncClient(
                        timeout=10,
                        follow_redirects=False,
                        trust_env=False,
                        headers={"User-Agent": UA},
                    ) as media:
                        register = (
                            (lambda kind, urls, digest: self.assets.add(scope, kind, urls, digest))
                            if scope
                            else None
                        )
                        return await preview_result(
                            result, start, media, deadline=deadline, register=register
                        )
        except (MediaError, httpx.HTTPError, OSError, ValueError, TimeoutError):
            return {
                "success": False,
                "content": "媒体解析或图片读取失败，未取得可用画面。",
                "content_items": [],
            }
        finally:
            self.tasks.discard(task)

    async def send(self, key, scope):
        if not self.enabled():
            return {"success": False, "content": "抖音媒体发送未启用"}
        if not scope or not isinstance(key, str) or len(key) != 32:
            return {"success": False, "content": "缺少当前聊天或媒体编号"}
        try:
            asset = self.assets.claim(scope, key)
        except ValueError as exc:
            return {"success": False, "content": str(exc)}
        task = asyncio.current_task()
        self.tasks.add(task)
        started = False
        try:
            async with asyncio.timeout(180 if asset.kind == "video" else 55):
                async with self.slots:
                    raw = None
                    local_video = None
                    async with httpx.AsyncClient(
                        timeout=10,
                        follow_redirects=False,
                        trust_env=False,
                        headers={"User-Agent": UA},
                    ) as client:
                        for url in asset.urls[:3]:
                            try:
                                if asset.kind == "video":
                                    if self.spool is None:
                                        raise MediaError("视频缓存未就绪")
                                    local_video = await self.spool.download(
                                        client, url, self.settings.media.video_max_mib * 1024 * 1024
                                    )
                                    break
                                candidate = await download_image(client, url)
                                await asyncio.to_thread(validate_original, candidate, asset.kind)
                                if (
                                    asset.digest
                                    and hashlib.sha256(candidate).hexdigest() != asset.digest
                                ):
                                    raise MediaError("原图已变化")
                                raw = candidate
                                break
                            except (MediaError, httpx.HTTPError, OSError, TimeoutError):
                                continue
                    if raw is None and local_video is None:
                        raise MediaError("原媒体无法读取")
                    asset.state = "unknown"
                    started = True
                    if asset.kind == "image":
                        sent = await self.context.send.image(
                            base64.b64encode(raw).decode("ascii"),
                            scope,
                            set_reply=False,
                            sync_to_maisaka_history=True,
                            maisaka_source_kind="neko_video_original",
                        )
                    else:
                        # Validated private file, readable by the same-host QQ
                        # client. Tiny RPC; never make QQ fetch an external URL.
                        sent = await self.context.call_capability(
                            "send.custom",
                            message_type="video",
                            content={"file": local_video.as_uri()},
                            stream_id=scope,
                            processed_plain_text="[视频]",
                            storage_message=False,
                            sync_to_maisaka_history=False,
                            maisaka_source_kind="neko_video_video",
                        )
                    if not (sent is True or isinstance(sent, dict) and sent.get("success") is True):
                        return {
                            "success": False,
                            "status": "delivery_unconfirmed",
                            "content": "已尝试投递但未确认，不自动重发。",
                        }
                    asset.state = "sent"
                    return {
                        "success": True,
                        "status": "sent",
                        "content": "平台已确认发送请求；原字节未重编码，QQ仍可能压缩或转码。",
                    }
        except asyncio.CancelledError:
            asset.state = "unknown" if started else "ready"
            raise
        except Exception:
            asset.state = "unknown" if started else "ready"
            return {
                "success": False,
                "status": "delivery_unconfirmed" if started else "unavailable",
                "content": "原媒体读取或发送未确认；图片上限8MiB，视频按配置及缓存/磁盘/时限保护。未知投递不自动重发，不以预览替代。",
            }
        finally:
            self.tasks.discard(task)
