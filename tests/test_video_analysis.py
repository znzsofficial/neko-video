import asyncio
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from neko_video.analysis_store import AnalysisStore
from neko_video.assets import MediaAssets
from neko_video.config import Config
from neko_video.media import MediaError
from neko_video.video_analysis import VideoAnalyzer
from neko_video.video_frames import metadata, times, command


class AnalysisTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.cfg = Config()
        self.cfg.plugin.enabled = True
        self.cfg.analysis.enabled = True
        self.assets = MediaAssets()
        self.key = self.assets.add("a", "video", ["https://example.com/video.mp4"])
        self.generate = AsyncMock(
            return_value={"content": "约1秒：蓝色，约2秒：红色。", "finish_reason": "stop"}
        )
        self.service = VideoAnalyzer(self.cfg, self.assets, self.directory.name, self.generate)
        self.addAsyncCleanup(self.service.close)
        self.created = []

        async def download(client, url, limit):
            self.assertEqual(limit, 64 * 1024**2)
            self.service.spool.directory.mkdir(parents=True, exist_ok=True)
            path = self.service.spool.directory / (
                "video-" + str(len(self.created)).zfill(32) + ".mp4"
            )
            path.write_bytes(b"synthetic mp4 body")
            self.created.append(path)
            return path

        self.fetch = patch.object(self.service.spool, "download", AsyncMock(side_effect=download))
        self.fetcher = self.fetch.start()
        self.addCleanup(self.fetch.stop)
        self.extraction = patch(
            "neko_video.video_analysis.extract",
            AsyncMock(
                return_value=(
                    {"duration_seconds": 3.0, "width": 640, "height": 360, "audio_analyzed": False},
                    [1.0, 2.0],
                    [b"jpeg-a", b"jpeg-b"],
                )
            ),
        )
        self.extractor = self.extraction.start()
        self.addCleanup(self.extraction.stop)
        self.binaries = patch("neko_video.video_frames.executable", side_effect=lambda name: name)
        self.binaries.start()
        self.addCleanup(self.binaries.stop)

    async def test_one_call_labels_actual_sampling_and_does_not_claim_send_handle(self):
        result = await self.service.read(self.key, "a")
        self.assertTrue(result["success"])
        self.assertFalse(result["full_video_coverage"])
        self.assertFalse(result["audio_analyzed"])
        self.assertEqual(result["sample_positions_seconds"], [1.0, 2.0])
        self.assertEqual(self.assets.items[self.key].state, "ready")
        self.generate.assert_awaited_once()
        parts = self.generate.call_args.args[0][0]["content"]
        self.assertEqual(sum(p["type"] == "image" for p in parts), 2)
        self.assertIn("1.000", parts[1]["text"])
        self.assertNotIn("example.com", str(result))
        self.assertFalse(any(path.exists() for path in self.created))

    async def test_success_cached_without_second_download_or_model_call(self):
        await self.service.read(self.key, "a")
        result = await self.service.read(self.key, "a")
        self.assertTrue(result["cache_hit"])
        self.fetcher.assert_awaited_once()
        self.generate.assert_awaited_once()

    async def test_new_handle_same_content_reuses_persistent_result_after_reload(self):
        await self.service.read(self.key, "a")
        self.service.store = None
        self.service.handles.clear()
        second = self.assets.add("a", "video", ["https://example.com/other.mp4"])
        result = await self.service.read(second, "a")
        self.assertTrue(result["cache_hit"])
        self.generate.assert_awaited_once()

    async def test_unknown_model_timeout_never_replays_even_with_new_handle(self):
        self.generate.side_effect = TimeoutError("private supplier detail")
        result = await self.service.read(self.key, "a")
        self.assertFalse(result["success"])
        again = await self.service.read(self.key, "a")
        self.assertFalse(again["success"])
        self.service.handles.clear()
        second = self.assets.add("a", "video", ["https://example.com/other.mp4"])
        self.assertFalse((await self.service.read(second, "a"))["success"])
        self.generate.assert_awaited_once()
        self.assertNotIn("private supplier detail", str(result))

    async def test_cancellation_after_admission_persists_unknown(self):
        entered = asyncio.Event()

        async def generate(prompt):
            entered.set()
            await asyncio.Event().wait()

        self.generate.side_effect = generate
        task = asyncio.create_task(self.service.read(self.key, "a"))
        await entered.wait()
        await self.service.close()
        self.assertTrue(task.cancelled())
        self.service.closed = False
        self.assertFalse((await self.service.read(self.key, "a"))["success"])
        self.generate.assert_awaited_once()

    async def test_wrong_scope_and_image_handle_denied_before_network(self):
        image = self.assets.add("a", "image", ["https://example.com/image.jpg"])
        for key, scope in [
            (self.key, "b"),
            (image, "a"),
            ("https://example.com/arbitrary", "a"),
            (self.key, ""),
        ]:
            self.assertFalse((await self.service.read(key, scope))["success"])
        self.fetcher.assert_not_awaited()
        self.generate.assert_not_awaited()

    async def test_disabled_and_missing_ffmpeg_do_not_download(self):
        self.cfg.analysis.enabled = False
        self.assertFalse((await self.service.read(self.key, "a"))["success"])
        self.cfg.analysis.enabled = True
        with patch("neko_video.video_frames.executable", side_effect=MediaError("binary missing")):
            self.assertFalse((await self.service.read(self.key, "a"))["success"])
        self.fetcher.assert_not_awaited()

    async def test_send_state_is_independent_of_analysis(self):
        self.assets.items[self.key].state = "unknown"
        self.assertTrue((await self.service.read(self.key, "a"))["success"])
        self.assertEqual(self.assets.items[self.key].state, "unknown")

    async def test_two_simultaneous_requests_one_model_call(self):
        values = await asyncio.gather(
            self.service.read(self.key, "a"), self.service.read(self.key, "a")
        )
        self.assertTrue(all(value["success"] for value in values))
        self.generate.assert_awaited_once()
        self.fetcher.assert_awaited_once()

    async def test_queue_limit_and_disable_while_waiting(self):
        await self.service.slot.acquire()
        jobs = [asyncio.create_task(self.service.read(self.key, "a")) for _ in range(4)]
        while len(self.service.tasks) < 4:
            await asyncio.sleep(0)
        self.assertFalse((await self.service.read(self.key, "a"))["success"])
        self.cfg.analysis.enabled = False
        self.service.slot.release()
        self.assertTrue(all(not value["success"] for value in await asyncio.gather(*jobs)))
        self.generate.assert_not_awaited()

    async def test_changed_hash_and_bad_frame_budget_fail_before_model(self):
        self.assets.items[self.key].digest = "f" * 64
        self.assertFalse((await self.service.read(self.key, "a"))["success"])
        self.assets.items[self.key].digest = ""
        self.extractor.return_value = ({"duration_seconds": 3}, [1], [b"x" * (6 * 180 * 1024 + 1)])
        self.assertFalse((await self.service.read(self.key, "a"))["success"])
        self.generate.assert_not_awaited()

    async def test_config_change_after_model_does_not_return_stale_text(self):
        async def generate(prompt):
            self.cfg.analysis.enabled = False
            return {"content": "private stale answer"}

        self.generate.side_effect = generate
        result = await self.service.read(self.key, "a")
        self.assertFalse(result["success"])
        self.assertNotIn("private stale answer", str(result))

    async def test_failure_empty_or_missing_model_body_not_cached_as_success(self):
        for value in [{"success": False, "error": "private"}, {}, {"content": ""}]:
            with tempfile.TemporaryDirectory() as other:
                self.service.directory = Path(other)
                self.service.store = None
                self.service.handles.clear()
                self.generate.return_value = value
                result = await self.service.read(self.key, "a")
                self.assertFalse(result["success"])

    async def test_output_limit_and_unknown_finish_reason_explicit(self):
        self.generate.return_value = {"content": "字" * 7000}
        result = await self.service.read(self.key, "a")
        self.assertTrue(result["output_truncated"])
        self.assertIsNone(result["model_truncated"])
        self.assertEqual(result["next_analysis_offset"], 6000)

    async def test_cached_analysis_continuation_never_loses_model_tail(self):
        self.generate.return_value = {"content": "a" * 6000 + "tail", "finish_reason": "stop"}
        first = await self.service.read(self.key, "a")
        tail = await self.service.read(self.key, "a", first["next_analysis_offset"])
        self.assertTrue(tail["cache_hit"])
        self.assertTrue(tail["content"].endswith("tail"))
        self.assertIsNone(tail["next_analysis_offset"])
        self.assertNotIn("_analysis_text", first)
        self.generate.assert_awaited_once()
        self.fetcher.assert_awaited_once()

    async def test_continuation_without_success_cache_never_calls_model(self):
        self.assertFalse((await self.service.read(self.key, "a", 1))["success"])
        self.assertFalse((await self.service.read(self.key, "a", True))["success"])
        self.generate.assert_not_awaited()
        self.extractor.assert_not_awaited()

    async def test_sending_unlimited_does_not_disable_analysis_byte_limit(self):
        self.cfg.media.video_max_mib = 0
        self.assertTrue((await self.service.read(self.key, "a"))["success"])
        self.assertEqual(self.fetcher.call_args.args[2], 64 * 1024**2)


class PersistentAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.now = 10000
        self.store = AnalysisStore(self.directory.name, clock=lambda: self.now)

    def test_unknown_durable_and_success_stored_separately(self):
        self.assertTrue(self.store.admit("key"))
        self.assertFalse(self.store.admit("key"))
        self.assertEqual(self.store.get("key"), {"state": "unknown"})
        self.store.complete("key", {"content": "result"})
        self.assertEqual(self.store.get("key")["result"]["content"], "result")

    def test_full_attempt_table_fail_closed_without_eviction(self):
        for i in range(128):
            self.assertTrue(self.store.admit(str(i)))
        with self.assertRaises(MediaError):
            self.store.admit("new")
        self.assertEqual(self.store.get("0")["state"], "unknown")

    def test_expiry_and_no_video_url_storage(self):
        key = hashlib.sha256(b"scope-content-options").hexdigest()
        self.store.admit(key)
        self.now += 3601
        self.assertIsNone(self.store.get(key))
        self.assertTrue(self.store.admit(key))
        self.assertNotIn(b"https://", self.store.path.read_bytes())

    def test_corrupt_database_preserved_not_replaced(self):
        self.store.path.write_bytes(b"corrupt db")
        with self.assertRaises(sqlite3.DatabaseError):
            AnalysisStore(self.directory.name)
        self.assertEqual(self.store.path.read_bytes(), b"corrupt db")


class FrameBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def test_metadata_and_sampling_bounds(self):
        body = {
            "format": {"duration": "3"},
            "streams": [{"codec_type": "video", "width": 320, "height": 240}],
        }
        self.assertEqual(metadata(json.dumps(body), 120)["duration_seconds"], 3)
        self.assertEqual(len(times(3, 6)), 3)
        self.assertTrue(all(0 < value < 3 for value in times(3, 6)))
        for value in ["nan", "inf", "0", "121", "-1"]:
            body["format"]["duration"] = value
            with self.assertRaises(MediaError):
                metadata(json.dumps(body), 120)

    async def test_child_stdout_limit_and_timeout(self):
        import sys

        with self.assertRaises(MediaError):
            await command([sys.executable, "-c", "print('x'*10000)"], 100, 5)
        with self.assertRaises(TimeoutError):
            await command([sys.executable, "-c", "import time;time.sleep(100)"], 100, 0.1)

    async def test_cancel_child_is_not_swallowed(self):
        import sys

        task = asyncio.create_task(
            command([sys.executable, "-c", "import time;time.sleep(100)"], 100, 10)
        )
        await asyncio.sleep(0.05)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
