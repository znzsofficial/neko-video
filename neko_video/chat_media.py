"""Chat-native video attachments: resolve via adapter API into a scoped asset.

The adapter flattens video segments to plain text, so the plugin reads the original
OneBot payload back via the public ``get_msg`` action and resolves a download URL
through ``get_file``. Only bounded metadata is kept; no cookies are reused.
"""

import asyncio
import hashlib
import re

VIDEO_TEXT = re.compile(r"\[视频\][^\n]{0,300}")
VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".avi", ".flv", ".webm")
MAX_NAME = 240


class ChatMediaError(ValueError):
    """Refusal with a user-facing reason; never retried automatically."""


def chat_video_name(message):
    """Extract the video file name from the adapter's flattened text marker."""
    text = str(message.get("processed_plain_text") or "")
    for match in VIDEO_TEXT.finditer(text):
        inner = match.group(0)
        name = re.search(r"文件:\s*([^\s，,]+\.+[A-Za-z0-9]{1,8})", inner)
        if name:
            candidate = name.group(1).replace("\\", "/").rsplit("/", 1)[-1][:MAX_NAME]
            if candidate.lower().endswith(VIDEO_EXTENSIONS):
                return candidate
    return ""


class ChatMediaService:
    """Resolves chat-native video messages to assets via public adapter actions."""

    def __init__(self, context, settings, assets):
        self.context = context
        self.settings = settings
        self.assets = assets
        self.slots = asyncio.Semaphore(2)

    async def _action_data(self, action, params):
        async with asyncio.timeout(30):
            response = await self.context.api.call(
                "adapter.napcat.action.call_data", action_name=action, params=params
            )
        if not isinstance(response, dict):
            raise ChatMediaError("QQ接口未返回消息详情")
        return response

    async def _video_segment(self, message_id):
        """Return the first video segment of the original OneBot payload."""
        detail = await self._action_data("get_msg", {"message_id": str(message_id)})
        segments = detail.get("message")
        if not isinstance(segments, list):
            raise ChatMediaError("QQ消息详情缺少消息段")
        found = []
        for segment in segments:
            if isinstance(segment, dict) and segment.get("type") == "video":
                found.append(segment.get("data") if isinstance(segment.get("data"), dict) else {})
        if not found:
            raise ChatMediaError("该消息没有聊天视频，请让用户重新发送")
        return found[0]

    async def resolve_url(self, data, group_id=""):
        """Return a downloadable http(s) URL for one video segment payload."""
        for key in ("url", "file_url"):
            value = str(data.get(key) or "")
            if value.startswith(("http://", "https://")):
                return value
        file_id = str(data.get("file_id") or "")
        if not re.fullmatch(r"[\w./:+-]{1,256}", file_id or ""):
            raise ChatMediaError("视频编号无效，请重新发送")
        params = {"file_id": file_id}
        if group_id:
            params["group_id"] = group_id
        try:
            resource = await self._action_data("get_file", params)
        except ChatMediaError:
            if not group_id:
                raise
            resource = await self._action_data("get_group_file_url", params)
        for key in ("url", "file", "file_url"):
            value = str(resource.get(key) or "")
            if value.startswith(("http://", "https://")):
                return value
        raise ChatMediaError("未能取得该视频的下载地址，请重新发送")

    async def register(self, message, scope, message_id=""):
        """Register one chat-native video message as a scoped video asset."""
        if not scope:
            raise ChatMediaError("缺少当前聊天")
        message_id = str(message_id or message.get("message_id") or "").strip()
        if not message_id or len(message_id) > 128:
            raise ChatMediaError("消息编号无效")
        name = chat_video_name(message)
        if not name:
            raise ChatMediaError("该消息没有聊天视频，请让用户重新发送")
        group_id = str(
            ((message.get("message_info") or {}).get("group_info") or {}).get("group_id") or ""
        )
        async with self.slots:
            data = await self._video_segment(message_id)
            url = await self.resolve_url(data, group_id)
        digest = hashlib.sha256((str(data.get("file_id") or url)).encode()).hexdigest()
        media_id = self.assets.add(scope, "video", (url,), digest)
        return {"media_id": media_id, "name": name}
