import asyncio
import base64
import hashlib
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from neko_video.assets import MediaAssets
from neko_video.config import Config
from neko_video.media import validate_original, MediaError, preview_result, rich_result
from neko_video.media_service import MediaService
from tests.test_media import image_bytes, fixture


class Handles(unittest.TestCase):
    def test_scope_expiry_and_claim_states(self):
        now = [0]
        assets = MediaAssets(clock=lambda: now[0])
        key = assets.add("chat", "image", ["https://image.example/a"], "digest")
        with self.assertRaises(ValueError):
            assets.claim("other", key)
        asset = assets.claim("chat", key)
        for state in ["busy", "unknown", "sent"]:
            asset.state = state
            with self.assertRaises(ValueError):
                assets.claim("chat", key)
        asset.state = "ready"
        now[0] = 600
        with self.assertRaises(ValueError):
            assets.claim("chat", key)

    def test_bounded_metadata_cache(self):
        assets = MediaAssets()
        first = assets.add("chat", "image", ["url"])
        for _ in range(24):
            assets.add("chat", "image", ["url"])
        self.assertNotIn(first, assets.items)
        self.assertEqual(len(assets.items), 24)
        for i in range(200):
            assets.add(str(i), "video", ["url"] * 20)
        self.assertEqual(len(assets.items), 128)
        self.assertTrue(all(len(x.urls) <= 8 for x in assets.items.values()))

    def test_container_validation_is_not_full_decode(self):
        validate_original(image_bytes(), "image")
        validate_original(b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00", "video")
        for value in [
            b"<html>bad</html>",
            b"\x00\x00\x00\x18ftypavif\x00\x00\x00\x00",
            b"x" * (8 * 1024 * 1024 + 1),
        ]:
            with self.assertRaises(MediaError):
                validate_original(value, "video")


class Delivery(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        config = Config()
        config.plugin.enabled = True
        context = SimpleNamespace(
            send=SimpleNamespace(image=AsyncMock(return_value=True)),
            call_capability=AsyncMock(return_value=True),
        )
        self.service = MediaService(config, context)
        self.raw = image_bytes()
        self.key = self.service.assets.add(
            "chat", "image", ["https://image.example/a"], hashlib.sha256(self.raw).hexdigest()
        )

    async def test_original_sent_once_without_reencoding(self):
        with patch("neko_video.media_service.download_image", AsyncMock(return_value=self.raw)):
            result = await self.service.send(self.key, "chat")
        self.assertTrue(result["success"])
        sent = self.service.context.send.image.call_args.args
        self.assertEqual(base64.b64decode(sent[0]), self.raw)
        self.assertFalse((await self.service.send(self.key, "chat"))["success"])
        self.service.context.send.image.assert_awaited_once()

    async def test_scope_and_disabled_reject_before_download(self):
        with patch("neko_video.media_service.download_image", AsyncMock()) as fetch:
            self.assertFalse((await self.service.send(self.key, "other"))["success"])
            self.assertFalse((await self.service.send(self.key, ""))["success"])
            self.service.settings.plugin.enabled = False
            self.assertFalse((await self.service.send(self.key, "chat"))["success"])
            fetch.assert_not_awaited()

    async def test_changed_source_never_sends_preview(self):
        with patch(
            "neko_video.media_service.download_image", AsyncMock(return_value=image_bytes((20, 20)))
        ):
            result = await self.service.send(self.key, "chat")
        self.assertFalse(result["success"])
        self.service.context.send.image.assert_not_awaited()
        self.assertEqual(self.service.assets.items[self.key].state, "ready")

    async def test_unknown_delivery_is_not_retried(self):
        self.service.context.send.image.return_value = {"success": False}
        with patch(
            "neko_video.media_service.download_image", AsyncMock(return_value=self.raw)
        ) as fetch:
            self.assertEqual(
                (await self.service.send(self.key, "chat"))["status"], "delivery_unconfirmed"
            )
            self.assertFalse((await self.service.send(self.key, "chat"))["success"])
            fetch.assert_awaited_once()

    async def test_cancel_after_send_starts_is_unknown(self):
        self.service.context.send.image.side_effect = asyncio.CancelledError
        with patch("neko_video.media_service.download_image", AsyncMock(return_value=self.raw)):
            with self.assertRaises(asyncio.CancelledError):
                await self.service.send(self.key, "chat")
        self.assertEqual(self.service.assets.items[self.key].state, "unknown")
        self.assertFalse(self.service.tasks)

    async def test_video_payload_single_copy_no_remote_platform_fetch_or_history(self):
        key = self.service.assets.add("chat", "video", ["https://video.example/video.mp4"])
        raw = b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00" + b"0" * (8 * 1024 * 1024 - 16)
        with patch("neko_video.media_service.download_image", AsyncMock(return_value=raw)):
            self.assertTrue((await self.service.send(key, "chat"))["success"])
        call = self.service.context.call_capability.call_args
        self.assertEqual(call.args[0], "send.custom")
        self.assertEqual(base64.b64decode(call.kwargs["content"]["binary_data_base64"]), raw)
        self.assertNotIn("data", call.kwargs)
        self.assertIs(call.kwargs["storage_message"], False)
        self.assertIs(call.kwargs["sync_to_maisaka_history"], False)
        self.assertLess(len(json.dumps(call.kwargs).encode()), 12 * 1024 * 1024)

    async def test_false_video_mapping_not_truthy_ack(self):
        key = self.service.assets.add("chat", "video", ["https://video.example/video.mp4"])
        self.service.context.call_capability.return_value = {"success": False}
        with patch(
            "neko_video.media_service.download_image",
            AsyncMock(return_value=b"\0\0\0\x18ftypisom\0\0\0\0"),
        ):
            self.assertFalse((await self.service.send(key, "chat"))["success"])
        self.assertEqual(self.service.assets.items[key].state, "unknown")

    async def test_preview_registers_hash_and_motion_handles(self):
        result = rich_result(fixture())
        result["images"][0]["video_urls"] = ["https://video.example/live"]

        async def fetch(*a, **kw):
            return self.raw

        def register(kind, urls, digest):
            return self.service.assets.add("chat", kind, urls, digest)

        preview = await preview_result(result, 0, None, fetch=fetch, register=register)
        meta = preview["content_items"][0]["metadata"]
        self.assertEqual(
            self.service.assets.items[meta["media_id"]].digest, hashlib.sha256(self.raw).hexdigest()
        )
        self.assertEqual(self.service.assets.items[meta["motion_id"]].kind, "video")
