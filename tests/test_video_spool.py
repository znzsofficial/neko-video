import asyncio
import os
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx

from neko_video.media import MediaError
from neko_video.video_spool import VideoSpool, FREE_BYTES
from tests.test_media import resolver

HEADER = b"\0\0\0\x18ftypisom\0\0\0\0"


class SpoolTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_during_disk_write_waits_for_writer_before_unlink(self):
        import threading

        started, release = threading.Event(), threading.Event()
        original = self.spool._write

        def slow_write(*args):
            started.set()
            release.wait(5)
            original(*args)

        async def source(*args):
            yield HEADER

        with (
            patch.object(self.spool, "_write", slow_write),
            patch("neko_video.video_spool.download_chunks", source),
        ):
            job = asyncio.create_task(self.spool.download(None, "https://video.example/a", 0))
            await asyncio.to_thread(started.wait, 5)
            job.cancel()
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await job
        self.assertFalse(list(self.spool.directory.iterdir()))

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.spool = VideoSpool(self.temp.name)

    async def download(self, chunks, limit=0):
        async def source(*args):
            for chunk in chunks:
                yield chunk

        with patch("neko_video.video_spool.download_chunks", source):
            return await self.spool.download(None, "https://video.example/a", limit)

    async def test_valid_video_over_old_cap_and_expiry_cleanup_is_scoped(self):
        path = await self.download([HEADER, *([b"a" * 256 * 1024] * 36)])
        self.assertGreater(path.stat().st_size, 8 * 1024 * 1024)
        self.assertFalse(list(path.parent.glob("*.part")))
        unrelated = path.parent / "keep.txt"
        unrelated.write_text("keep", encoding="utf-8")
        os.utime(path, (1, 1))
        os.utime(unrelated, (1, 1))
        await self.spool.clean()
        self.assertFalse(path.exists())
        self.assertTrue(unrelated.exists())

    async def test_limit_invalid_container_and_low_disk_leave_no_partial_files(self):
        for chunks, limit in [([HEADER, b"a" * 1000], 100), ([b"not a video at all"], 0)]:
            with self.assertRaises(MediaError):
                await self.download(chunks, limit)
            self.assertFalse(list(self.spool.directory.iterdir()))
        with patch(
            "neko_video.video_spool.shutil.disk_usage",
            return_value=SimpleNamespace(free=FREE_BYTES),
        ):
            with self.assertRaises(MediaError):
                await self.download([HEADER])
        self.assertFalse(list(self.spool.directory.iterdir()))

    async def test_cache_capacity_applies_even_with_unlimited_single_file(self):
        with patch("neko_video.video_spool.CACHE_BYTES", 100):
            with self.assertRaises(MediaError):
                await self.download([HEADER, b"a" * 100])
        self.assertFalse(list(self.spool.directory.iterdir()))

    async def test_cancel_mid_stream_closes_fd_and_removes_partial(self):
        ready = asyncio.Event()

        async def source(*args):
            yield HEADER
            ready.set()
            await asyncio.Future()

        with patch("neko_video.video_spool.download_chunks", source):
            job = asyncio.create_task(self.spool.download(None, "https://video.example/a", 0))
            await ready.wait()
            job.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await job
        self.assertFalse(self.spool.active)
        self.assertFalse(list(self.spool.directory.iterdir()))

    async def test_public_download_preserves_dns_host_sni_without_cookies(self):
        from neko_video.media import download_chunks

        def handler(request):
            self.assertEqual(request.url.host, "8.8.8.8")
            self.assertEqual(request.headers["host"], "cdn.example")
            self.assertEqual(request.extensions["sni_hostname"], b"cdn.example")
            self.assertEqual(request.headers.get("cookie"), "")
            return httpx.Response(200, content=HEADER + b"a" * 2048)

        async def chunks(client, url):
            async for part in download_chunks(client, url, resolver=resolver):
                yield part

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), trust_env=False
        ) as client:
            with patch("neko_video.video_spool.download_chunks", chunks):
                path = await self.spool.download(client, "https://cdn.example/a", 0)
        self.assertEqual(path.read_bytes(), HEADER + b"a" * 2048)
