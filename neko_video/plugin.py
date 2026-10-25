"""Thin MaiBot adapter: components, chat context and lifecycle only."""

import asyncio
import base64
from pathlib import Path

import httpx
from maibot_sdk import Command, HookHandler, MaiBotPlugin, Tool
from maibot_sdk.types import ErrorPolicy, HookMode, HookOrder, ToolParameterInfo, ToolParamType

from .config import Config
from .credentials import CredentialService
from .media_service import MediaService
from .official import OfficialVideoService
from .presentation import render
from .transport import UA
from .video_analysis import VideoAnalyzer


def scope_of(kwargs):
    return str(
        kwargs.get("stream_id") or kwargs.get("session_id") or kwargs.get("chat_id") or ""
    ).strip()


def sender_of(explicit, kwargs):
    value = (
        explicit
        or kwargs.get("user_id")
        or ((kwargs.get("message") or {}).get("message_info") or {})
        .get("user_info", {})
        .get("user_id")
    )
    value = str(value or "").strip()
    return value[3:].strip() if value.lower().startswith("qq:") else value


class NekoVideoPlugin(MaiBotPlugin):
    config_model = Config

    def __init__(self):
        super().__init__()
        self.client = None
        self.credentials = None
        self.official = None
        self.media = None
        self.poll_task = None
        self.tasks = set()
        self.lifecycle = asyncio.Lock()
        self.login_start = asyncio.Lock()
        self.ready = False
        self.analyzer = None

    async def on_load(self):
        async with self.lifecycle:
            await self._open()
        self.ctx.logger.info("Neko Video 已加载")

    async def _open(self):
        directory = Path(self.ctx.paths.data_dir)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.client = httpx.AsyncClient(
            timeout=12, follow_redirects=False, trust_env=False, headers={"User-Agent": UA}
        )
        self.credentials = CredentialService(directory, self.config.credential, self.client)
        self.official = OfficialVideoService(self.config.parse, self.credentials, self.client)
        self.media = MediaService(self.config, self.ctx)
        await self.media.start()
        self.analyzer = VideoAnalyzer(
            self.config, self.media.assets, directory, self._visual_generate, self._transcribe_audio
        )
        self.ready = True

    async def _close(self):
        self.ready = False
        if self.analyzer is not None:
            await self.analyzer.close()
        if self.media is not None:
            await self.media.close()
        jobs = [
            task
            for task in [self.poll_task, *self.tasks]
            if task is not None and task is not asyncio.current_task()
        ]
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        self.tasks.clear()
        self.poll_task = None
        if self.client is not None:
            await self.client.aclose()
        self.client = None

    async def on_unload(self):
        async with self.lifecycle:
            await self._close()

    async def on_config_update(self, scope, config_data, version):
        del scope, config_data, version
        async with self.lifecycle:
            await self._close()
            await self._open()

    def enabled(self):
        return self.ready and self.config.plugin.enabled

    async def _visual_generate(self, prompt):
        return await self.ctx.llm.generate(
            prompt, task_name="vlm", max_tokens=2500, temperature=0.2
        )

    async def _transcribe_audio(self, audio_base64):
        return await self.ctx.llm.transcribe_audio(
            audio_base64=audio_base64,
            task_name="voice",
            rpc_timeout_ms=self.config.asr.timeout_seconds * 1000,
        )

    async def _summary(self, link, expected=None, timeout=40):
        if not self.enabled():
            return None
        task = asyncio.current_task()
        self.tasks.add(task)
        try:
            async with asyncio.timeout(timeout):
                return await self.official.summary(link, expected)
        finally:
            self.tasks.discard(task)

    @HookHandler(
        "chat.receive.after_process",
        name="neko_video_context",
        description="为分享链接附加平台文字资料",
        mode=HookMode.BLOCKING,
        order=HookOrder.NORMAL,
        timeout_ms=10000,
        error_policy=ErrorPolicy.SKIP,
    )
    async def inject(self, **kwargs):
        message = kwargs.get("message")
        if not self.enabled() or not isinstance(message, dict) or message.get("is_notify"):
            return {"action": "continue"}
        group = bool((message.get("message_info") or {}).get("group_info"))
        if not (
            self.config.parse.enable_in_group if group else self.config.parse.enable_in_private
        ):
            return {"action": "continue"}
        text = str(message.get("processed_plain_text") or "")
        if not text:
            text = " ".join(
                x["data"]
                for x in message.get("raw_message") or []
                if isinstance(x, dict)
                and x.get("type") == "text"
                and isinstance(x.get("data"), str)
            )
        if not text or "[Neko Video]" in text:
            return {"action": "continue"}
        try:
            info = await self._summary(text, timeout=9)
        except Exception:
            return {"action": "continue"}
        if not info:
            return {"action": "continue"}
        block = "[Neko Video]\n" + render(info, injected=True)
        updated = dict(message)
        updated["processed_plain_text"] = (text + "\n" + block).strip()
        if isinstance(message.get("raw_message"), list):
            updated["raw_message"] = [
                *message["raw_message"],
                {"type": "text", "data": "\n" + block},
            ]
        return {"action": "continue", "modified_kwargs": {"message": updated}}

    async def _summary_tool(self, link, expected):
        if not self.enabled():
            return {"success": False, "content": "插件未启用"}
        try:
            info = await self._summary(link, expected)
            if info:
                return {"success": True, "content": render(info)}
        except Exception:
            pass
        return {"success": False, "content": "未取得有效视频资料，未猜测画面。"}

    @Tool(
        "neko_video_bilibili",
        description="按需读取B站视频信息与官方总结。",
        timeout_ms=45000,
        parameters=[
            ToolParameterInfo(
                name="link",
                param_type=ToolParamType.STRING,
                description="B站链接、BV/av号或短链",
                required=True,
            )
        ],
    )
    async def bilibili(self, link="", **kwargs):
        del kwargs
        return await self._summary_tool(link, "bilibili")

    @Tool(
        "neko_video_douyin",
        description="读取抖音官方总结；看图用neko_video_read。",
        timeout_ms=45000,
        parameters=[
            ToolParameterInfo(
                name="link",
                param_type=ToolParamType.STRING,
                description="抖音分享链接或ID",
                required=True,
            )
        ],
    )
    async def douyin(self, link="", **kwargs):
        del kwargs
        return await self._summary_tool(link, "douyin")

    @Tool(
        "neko_video_read",
        description="看图与取媒体编号；显式分析本聊天视频。",
        timeout_ms=130000,
        parameters=[
            ToolParameterInfo(
                name="link",
                param_type=ToolParamType.STRING,
                description="普通预览填抖音分享文本、链接或19位ID；分析时不填，先取得video media_id",
                required=False,
            ),
            ToolParameterInfo(
                name="analyze_video",
                param_type=ToolParamType.BOOLEAN,
                description="仅用户明确要求理解视频时设true，并配合本聊天video的media_id；最多6帧，不自动重试",
                required=False,
            ),
            ToolParameterInfo(
                name="media_id",
                param_type=ToolParamType.STRING,
                description="本聊天视频/实况的media_id，仅配合analyze_video=true；不接受URL/路径/封面编号",
                required=False,
            ),
            ToolParameterInfo(
                name="image_start",
                param_type=ToolParamType.INTEGER,
                description="图片起点，从0开始，每次最多4张",
                required=False,
            ),
            ToolParameterInfo(
                name="include_audio",
                param_type=ToolParamType.BOOLEAN,
                description="分析时同时转写音轨；无音轨自动跳过，不会重试",
                required=False,
            ),
            ToolParameterInfo(
                name="analysis_offset",
                param_type=ToolParamType.INTEGER,
                description="分析正文续读偏移，首次0，续读填上次返回的next_analysis_offset；不重新调用模型",
                required=False,
            ),
        ],
    )
    async def read(
        self,
        link="",
        image_start=0,
        analyze_video=False,
        media_id="",
        analysis_offset=0,
        include_audio=False,
        **kwargs,
    ):
        if not self.enabled():
            return {"success": False, "content": "插件未启用", "content_items": []}
        if type(analyze_video) is not bool or type(include_audio) is not bool:
            return {
                "success": False,
                "content": "analyze_video/include_audio必须是布尔值",
                "content_items": [],
            }
        if analyze_video:
            if link or type(image_start) is not int or image_start != 0:
                return {
                    "success": False,
                    "content": "分析只填analyze_video=true与当前聊天视频media_id，不混填link或图片起点",
                    "content_items": [],
                }
            return await self.analyzer.read(
                media_id, scope_of(kwargs), analysis_offset, include_audio
            )
        if media_id or analysis_offset or include_audio:
            return {
                "success": False,
                "content": "分析媒体编号时需显式analyze_video=true，未自动调用模型",
                "content_items": [],
            }
        return await self.media.read(link, image_start, scope_of(kwargs))

    @Tool(
        "neko_video_send",
        description="发送本聊天媒体编号对应的原图、视频或实况。",
        platforms=["qq"],
        timeout_ms=190000,
        parameters=[
            ToolParameterInfo(
                name="media_id",
                param_type=ToolParamType.STRING,
                description="读取结果的media_id",
                required=True,
            )
        ],
    )
    async def send(self, media_id="", **kwargs):
        if not self.enabled():
            return {"success": False, "content": "插件未启用"}
        return await self.media.send(media_id, scope_of(kwargs))

    def admin(self, user_id, kwargs):
        sender = sender_of(user_id, kwargs)
        allowed = {
            str(x).removeprefix("qq:").strip()
            for x in self.config.permission.admin_users
            if str(x).strip()
        }
        return bool(sender and allowed and sender in allowed)

    @Command("neko_video_login", description="管理员扫码登录B站", pattern=r"^/neko-video\s+login$")
    async def login(self, stream_id="", user_id="", **kwargs):
        if not self.enabled() or not self.admin(user_id, kwargs) or not stream_id:
            return False, "登录未就绪或无管理员权限", True
        async with self.login_start:
            if not self.enabled():
                return False, "插件未就绪", True
            if self.poll_task is not None and not self.poll_task.done():
                return False, "已有登录流程进行中", True
            task = asyncio.current_task()
            self.tasks.add(task)
            try:
                return await self._start_login(stream_id)
            finally:
                self.tasks.discard(task)

    async def _start_login(self, stream_id):
        try:
            png = await self.credentials.qrcode()
            sent = await self.ctx.send.image(base64.b64encode(png).decode(), stream_id)
            if not (sent is True or isinstance(sent, dict) and sent.get("success") is True):
                return False, "二维码投递未确认", True
            self.poll_task = asyncio.create_task(self._poll(stream_id))
            return True, "请在3分钟内用B站客户端扫码", True
        except Exception:
            return False, "登录二维码生成失败", True

    async def _poll(self, stream):
        try:
            result = await self.credentials.poll()
            await self.ctx.send.text(result, stream)
        except asyncio.CancelledError:
            raise
        except Exception:
            await self.ctx.send.text("扫码状态未确认，请稍后重试", stream)

    @Command(
        "neko_video_status", description="管理员查看视频插件状态", pattern=r"^/neko-video\s+status$"
    )
    async def status(self, user_id="", **kwargs):
        if not self.enabled() or not self.admin(user_id, kwargs):
            return False, "无管理员权限或插件未启用", True
        try:
            return True, await self.credentials.status(), True
        except Exception:
            return False, "凭据状态未确认，已保留原文件", True

    @Command(
        "neko_video_logout", description="管理员清除B站扫码登录", pattern=r"^/neko-video\s+logout$"
    )
    async def logout(self, user_id="", **kwargs):
        if not self.enabled() or not self.admin(user_id, kwargs):
            return False, "无管理员权限或插件未启用", True
        if self.poll_task is not None:
            self.poll_task.cancel()
            await asyncio.gather(self.poll_task, return_exceptions=True)
            self.poll_task = None
        try:
            return True, await self.credentials.logout(), True
        except Exception:
            return False, "登出未完成，请检查私有凭据文件", True
