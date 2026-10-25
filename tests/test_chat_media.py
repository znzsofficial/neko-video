"""Chat-native video attachment registration; adapter API mocked, no network."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from neko_video.assets import MediaAssets
from neko_video.chat_media import ChatMediaError, ChatMediaService, video_records


def message_with(video=None, group="178687944", account="3392089897"):
    extra = {}
    if video is not None:
        extra["qq_video_attachments"] = video
    return {
        "session_id": "stream-a",
        "account_id": account,
        "message_info": {
            "group_info": {"group_id": group} if group else None,
            "user_info": {"user_id": "10001"},
            "additional_config": extra,
        },
    }


class RecordExtraction(unittest.TestCase):
    def test_records_bounded_and_normalized(self):
        videos = [
            {"file_id": "", "url": "https://example.com/v.mp4", "file": "a.mp4", "file_size": 12},
            {"file_id": "ABC123", "file_size": "bad"},
            "junk",
            {"file_id": "D{0}".format("x" * 300), "file_name": "deep/dir/b.mp4", "file_size": 5},
        ]
        records = video_records(message_with(videos))
        self.assertEqual(len(records), 3)
        self.assertEqual(records[0]["url"], "https://example.com/v.mp4")
        self.assertEqual(records[1]["size"], 0)
        self.assertEqual(len(records[2]["file_id"]), 256)
        self.assertEqual(records[2]["name"], "b.mp4")

    def test_records_reject_non_video_and_missing_ident(self):
        self.assertEqual(video_records(message_with([{"name": "x.pdf"}])), [])
        self.assertEqual(video_records(message_with([{"url": "ftp://nope"}])), [])
        self.assertEqual(video_records(message_with("not-a-list")), [])


class RegisterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.assets = MediaAssets()
        self.ctx = SimpleNamespace(api=SimpleNamespace(call=AsyncMock()))
        self.service = ChatMediaService(self.ctx, object(), self.assets)

    async def test_direct_url_registers_without_adapter_call(self):
        record = await self.service.register(
            message_with(
                [{"url": "https://cdn.example.com/v.mp4", "file": "clip.mp4", "file_size": 100}]
            ),
            "stream-a",
        )
        self.ctx.api.call.assert_not_awaited()
        asset = self.assets.peek("stream-a", record["media_id"])
        self.assertEqual(asset.kind, "video")
        self.assertEqual(asset.urls, ("https://cdn.example.com/v.mp4",))

    async def test_file_id_resolves_via_get_file_then_fallback(self):
        self.ctx.api.call.side_effect = [
            {"status": "failed"},
            {"status": "ok", "data": {"url": "https://ftn.qq.com/dl?v=1"}},
        ]
        record = await self.service.register(
            message_with([{"file_id": "VID1", "file": "native.mp4", "file_size": 2048}]),
            "stream-a",
        )
        self.assertEqual(self.ctx.api.call.await_count, 2)
        self.assertEqual(
            self.assets.peek("stream-a", record["media_id"]).urls[0],
            "https://ftn.qq.com/dl?v=1",
        )
        actions = [c.kwargs["action_name"] for c in self.ctx.api.call.await_args_list]
        self.assertEqual(actions, ["get_file", "get_group_file_url"])

    async def test_invalid_index_and_bad_responses_rejected(self):
        with self.assertRaises(ChatMediaError):
            await self.service.register(message_with([{"url": "https://x/v.mp4"}]), "stream-a", 5)
        self.ctx.api.call.return_value = {"status": "ok", "data": {"file": "/local/path.mp4"}}
        with self.assertRaises(ChatMediaError):
            await self.service.register(
                message_with([{"file_id": "VID2", "file_size": 1}]), "stream-a"
            )
        self.assets.items.clear()
        self.assertEqual(len(self.assets.items), 0)

    async def test_suspicious_file_id_rejected_before_any_call(self):
        with self.assertRaises(ChatMediaError):
            await self.service.register(
                message_with([{"file_id": "../escape?x", "file_size": 1}]), "stream-a"
            )
        self.ctx.api.call.assert_not_awaited()

    async def test_cross_scope_media_id_rejected(self):
        record = await self.service.register(
            message_with([{"url": "https://cdn.example.com/v.mp4", "file_size": 1}]), "stream-a"
        )
        with self.assertRaises(ValueError):
            self.assets.peek("stream-b", record["media_id"])
