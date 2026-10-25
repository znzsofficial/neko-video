"""Chat-native video registration via get_msg; adapter API mocked, no network."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from neko_video.assets import MediaAssets
from neko_video.chat_media import ChatMediaError, ChatMediaService, chat_video_name
from neko_video.config import Config
from neko_video.plugin import NekoVideoPlugin


def message(text="[视频] 文件: 4b95.mp4，大小: 1403651", group="123", mid="1251563450"):
    return {
        "session_id": "stream-a",
        "message_id": mid,
        "account_id": "789",
        "processed_plain_text": text,
        "message_info": {
            "group_info": {"group_id": group} if group else None,
            "user_info": {"user_id": "10001"},
        },
    }


def get_msg_response(url="", file_id="VID1", name="4b95.mp4"):
    data = {"file": name, "file_id": file_id, "file_size": 1403651}
    if url:
        data["url"] = url
    return {"message_id": "1251563450", "message": [{"type": "video", "data": data}]}


class NameExtraction(unittest.TestCase):
    def test_extracts_video_name_from_flattened_marker(self):
        self.assertEqual(chat_video_name(message()), "4b95.mp4")
        self.assertEqual(
            chat_video_name(message(text="看这个\n[视频] 文件: clip.9495.mp4，大小: 9 \n后面")),
            "clip.9495.mp4",
        )

    def test_rejects_plain_text_and_non_video_names(self):
        self.assertEqual(chat_video_name(message(text="没有视频的普通消息")), "")
        self.assertEqual(chat_video_name(message(text="[视频] 文件: notes.pdf，大小: 1")), "")


class RegisterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.assets = MediaAssets()
        self.ctx = SimpleNamespace(api=SimpleNamespace(call=AsyncMock()))
        self.service = ChatMediaService(self.ctx, object(), self.assets)

    async def test_segment_url_registers_directly(self):
        self.ctx.api.call.return_value = get_msg_response(url="https://cdn.example.com/v.mp4")
        record = await self.service.register(message(), "stream-a")
        self.assertEqual(self.ctx.api.call.await_count, 1)
        asset = self.assets.peek("stream-a", record["media_id"])
        self.assertEqual(asset.kind, "video")
        self.assertEqual(asset.urls, ("https://cdn.example.com/v.mp4",))
        # Content digest stays empty: analysis hashes downloaded bytes itself and
        # a non-content digest would make that verification always fail.
        self.assertEqual(asset.digest, "")
        self.assertEqual(record["name"], "4b95.mp4")

    async def test_file_id_falls_back_to_get_group_file_url(self):
        self.ctx.api.call.side_effect = [
            get_msg_response(),
            {"file": "https://ftn.qq.com/dl?v=1"},
        ]
        record = await self.service.register(message(), "stream-a")
        actions = [c.kwargs["action_name"] for c in self.ctx.api.call.await_args_list]
        self.assertEqual(actions, ["get_msg", "get_file"])
        self.assertEqual(
            self.assets.peek("stream-a", record["media_id"]).urls[0],
            "https://ftn.qq.com/dl?v=1",
        )

    async def test_private_message_without_group_raises_without_fallback(self):
        self.ctx.api.call.side_effect = [
            get_msg_response(),
            {"file": "/local/path.mp4"},
        ]
        with self.assertRaises(ChatMediaError):
            await self.service.register(message(group=""), "stream-a")
        actions = [c.kwargs["action_name"] for c in self.ctx.api.call.await_args_list]
        self.assertEqual(actions, ["get_msg", "get_file"])

    async def test_no_video_segment_or_bad_payload_rejected(self):
        self.ctx.api.call.return_value = {"message": [{"type": "text", "data": "hi"}]}
        with self.assertRaises(ChatMediaError):
            await self.service.register(message(), "stream-a")
        self.ctx.api.call.return_value = {"message": "flat"}
        with self.assertRaises(ChatMediaError):
            await self.service.register(message(), "stream-a")

    async def test_suspicious_file_id_rejected_before_get_file(self):
        self.ctx.api.call.return_value = get_msg_response(file_id="../escape?x")
        with self.assertRaises(ChatMediaError):
            await self.service.register(message(), "stream-a")

    async def test_message_without_video_marker_rejected_without_api_call(self):
        self.ctx.api.call.return_value = get_msg_response()
        with self.assertRaises(ChatMediaError):
            await self.service.register(message(text="plain"), "stream-a")
        self.ctx.api.call.assert_not_awaited()

    async def test_cross_scope_media_id_rejected(self):
        self.ctx.api.call.return_value = get_msg_response(url="https://cdn.example.com/v.mp4")
        record = await self.service.register(message(), "stream-a")
        with self.assertRaises(ValueError):
            self.assets.peek("stream-b", record["media_id"])


class ChatToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import tempfile

        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

        class Plugin(NekoVideoPlugin):
            config = Config()
            ctx = SimpleNamespace(
                paths=SimpleNamespace(data_dir=self.directory.name),
                logger=SimpleNamespace(info=lambda *a: None),
                send=SimpleNamespace(image=AsyncMock(return_value=True)),
                api=SimpleNamespace(call=AsyncMock()),
            )

        self.plugin = Plugin()
        await self.plugin.on_load()
        self.addAsyncCleanup(self.plugin.on_unload)

    def enable(self):
        self.plugin.config.plugin.enabled = True
        self.plugin.config.parse.enable_douyin = True
        self.plugin.config.media.enabled = True

    async def test_capability_denial_is_not_reported_as_missing_message(self):

        self.enable()

        def denied(*args, **kwargs):
            raise PermissionError("capability not declared")

        self.plugin.ctx.call_capability = denied
        result = await self.plugin.chat_video(source_message_id="1251563450", stream_id="stream-a")
        self.assertFalse(result["success"])
        self.assertIn("消息读取能力被拒绝", result["content"])
        self.assertNotIn("找不到该消息", result["content"])

    async def test_focus_turn_explicit_session_wins_over_injected_stream(self):
        self.enable()
        self.plugin.ctx.call_capability = AsyncMock(
            return_value={"success": True, "message": message()}
        )
        self.plugin.ctx.api.call.return_value = get_msg_response(
            url="https://cdn.example.com/v.mp4"
        )
        # Focus turn: model passes the target session; host injected another stream.
        result = await self.plugin.chat_video(
            source_message_id="1251563450",
            stream_id="triggering-private-chat",
            session_id="stream-a",
        )
        self.assertTrue(result["success"])
        args = self.plugin.ctx.call_capability.await_args.kwargs
        self.assertEqual(args["chat_id"], "stream-a")

    async def test_normal_turn_uses_injected_stream_when_no_explicit_session(self):
        self.enable()
        self.plugin.ctx.call_capability = AsyncMock(return_value={"success": False})
        result = await self.plugin.chat_video(source_message_id="42", stream_id="stream-b")
        self.assertFalse(result["success"])
        self.assertEqual(self.plugin.ctx.call_capability.await_args.kwargs["chat_id"], "stream-b")

    async def test_missing_message_still_reports_not_found(self):
        self.enable()
        self.plugin.ctx.call_capability = AsyncMock(
            return_value={"success": False, "error": "not found"}
        )
        result = await self.plugin.chat_video(source_message_id="999", stream_id="stream-a")
        self.assertFalse(result["success"])
        self.assertIn("找不到该消息", result["content"])

    async def test_full_path_via_real_message_shape(self):

        self.enable()
        self.plugin.ctx.call_capability = AsyncMock(
            return_value={"success": True, "message": message()}
        )
        self.plugin.ctx.api.call.return_value = get_msg_response(
            url="https://cdn.example.com/v.mp4"
        )
        result = await self.plugin.chat_video(source_message_id="1251563450", stream_id="stream-a")
        self.assertTrue(result["success"])
        self.assertIn("media_id", result)
        args = self.plugin.ctx.call_capability.await_args.kwargs
        self.assertEqual(args["chat_id"], "stream-a")
        self.assertEqual(args["message_id"], "1251563450")
