"""Chat-native video attachments: resolve via adapter API into a scoped asset.

The host converts video segments to plain text, so the plugin re-reads the
message by ID and uses public NapCat file actions for a downloadable URL.
No cookies are reused; only bounded metadata is kept.
"""

import asyncio
import hashlib
import re

VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".avi", ".flv", ".webm")
MAX_NAME = 240


class ChatMediaError(ValueError):
    """Refusal with a user-facing reason; never retried automatically."""


def video_records(message):
    """Extract bounded video attachment records from a host message dict."""
    info = message.get("message_info") or {}
    extra = info.get("additional_config") or {}
    raw = extra.get("qq_video_attachments") or []
    if not isinstance(raw, list):
        return []
    group = str((info.get("group_info") or {}).get("group_id") or "")
    user = str((info.get("user_info") or {}).get("user_id") or "")
    records = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        file_id = str(item.get("file_id") or "")[:256]
        url = str(item.get("url") or "")
        if not file_id and not url.startswith(("http://", "https://")):
            continue
        name = str(item.get("file") or item.get("name") or item.get("file_name") or "视频.mp4")
        name = name.replace("\\", "/").rsplit("/", 1)[-1][:MAX_NAME] or "视频.mp4"
        try:
            size = int(item.get("file_size") or item.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        records.append(
            {
                "file_id": file_id,
                "url": url if url.startswith(("http://", "https://")) else "",
                "name": name,
                "size": max(size, 0),
                "group_id": group,
                "user_id": user,
                "account_id": str(message.get("account_id") or ""),
            }
        )
    return records[:8]


def looks_like_video_name(name):
    return str(name or "").lower().endswith(VIDEO_EXTENSIONS)


class ChatMediaService:
    """Resolves chat video attachments to assets via public adapter actions."""

    def __init__(self, context, settings, assets):
        self.context = context
        self.settings = settings
        self.assets = assets
        self.slots = asyncio.Semaphore(2)

    async def _action(self, action, params):
        async with asyncio.timeout(30):
            response = await self.context.api.call(
                "adapter.napcat.action.call", action_name=action, params=params
            )
        if not isinstance(response, dict) or response.get("status") not in (None, "ok", "async"):
            raise ChatMediaError("QQ文件接口不可用或无权限，请重新发送视频")
        data = response.get("data", response)
        if not isinstance(data, dict):
            raise ChatMediaError("QQ文件接口未返回视频资源")
        return data

    async def resolve_url(self, record):
        """Return a downloadable http(s) URL for one video record."""
        if record["url"]:
            return record["url"]
        if not re.fullmatch(r"[\w./:+-]{1,256}", record["file_id"]):
            raise ChatMediaError("视频编号无效，请重新发送")
        params = {"file_id": record["file_id"]}
        if record["group_id"]:
            params["group_id"] = record["group_id"]
        try:
            data = await self._action("get_file", params)
        except ChatMediaError:
            if not record["group_id"]:
                raise
            data = await self._action("get_group_file_url", params)
        for key in ("url", "file", "file_url"):
            value = str(data.get(key) or "")
            if value.startswith(("http://", "https://")):
                return value
        raise ChatMediaError("未能取得该视频的下载地址，请重新发送")

    async def register(self, message, scope, media_index=0):
        """Register one chat video as a scoped video asset; return the media_id."""
        if not scope:
            raise ChatMediaError("缺少当前聊天")
        if isinstance(media_index, bool) or not isinstance(media_index, int):
            raise ChatMediaError("视频序号无效")
        records = video_records(message)
        if not 0 <= media_index < len(records):
            raise ChatMediaError("该消息没有可读取的聊天视频附件")
        record = records[media_index]
        async with self.slots:
            url = await self.resolve_url(record)
        digest = hashlib.sha256((record["file_id"] or record["url"]).encode()).hexdigest()
        media_id = self.assets.add(scope, "video", (url,), digest)
        return {
            "media_id": media_id,
            "name": record["name"],
            "size": record["size"],
            "is_video_file": True,
        }
