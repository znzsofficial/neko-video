import asyncio
import base64
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
        self.transcribe = AsyncMock(return_value={"success": True, "text": "这是一段合成转写。"})
        self.service = VideoAnalyzer(
            self.cfg, self.assets, self.directory.name, self.generate, self.transcribe
        )
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
                    {
                        "duration_seconds": 3.0,
                        "width": 640,
                        "height": 360,
                        "audio_analyzed": False,
                        "has_audio_track": True,
                    },
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
        self.audio_patch = patch(
            "neko_video.video_analysis.extract_audio",
            AsyncMock(return_value=(b"synthetic wav", 3.0)),
        )
        self.audio_extract = self.audio_patch.start()
        self.addCleanup(self.audio_patch.stop)

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
        self.audio_extract.assert_not_awaited()
        self.transcribe.assert_not_awaited()

    async def test_asr_disabled_rejects_before_download_and_both_models(self):
        result = await self.service.read(self.key, "a", include_audio=True)
        self.assertFalse(result["success"])
        self.fetcher.assert_not_awaited()
        self.generate.assert_not_awaited()
        self.transcribe.assert_not_awaited()

    async def test_audio_opt_in_one_asr_and_visual_call_then_cached(self):
        self.cfg.asr.enabled = True
        first = await self.service.read(self.key, "a", include_audio=True)
        self.assertTrue(first["success"])
        self.assertTrue(first["audio_analyzed"])
        self.assertEqual(first["audio_status"], "transcribed")
        self.assertEqual(first["audio_duration_seconds"], 3.0)
        self.assertEqual(first["audio_track"], "first")
        self.assertFalse(first["transcript_verified"])
        self.assertIsNone(first["transcript_truncated"])
        self.assertIn("这是一段合成转写", first["content"])
        self.assertNotIn("未分析音频", first["content"])
        self.transcribe.assert_awaited_once_with(base64.b64encode(b"synthetic wav").decode("ascii"))
        self.generate.assert_awaited_once()
        self.assertNotIn("这是一段合成转写", str(self.generate.call_args))
        self.assertTrue((await self.service.read(self.key, "a", include_audio=True))["cache_hit"])
        self.transcribe.assert_awaited_once()
        self.generate.assert_awaited_once()
        self.fetcher.assert_awaited_once()
        self.assertEqual(self.assets.items[self.key].state, "ready")

    async def test_no_audio_track_reports_absent_without_asr(self):
        self.cfg.asr.enabled = True
        self.extractor.return_value[0]["has_audio_track"] = False
        result = await self.service.read(self.key, "a", include_audio=True)
        self.assertTrue(result["success"])
        self.assertFalse(result["audio_analyzed"])
        self.assertEqual(result["audio_status"], "no_track")
        self.assertIn("未发现音轨", result["content"])
        self.audio_extract.assert_not_awaited()
        self.transcribe.assert_not_awaited()
        self.generate.assert_awaited_once()

    async def test_unknown_track_metadata_refuses_before_any_model(self):
        self.cfg.asr.enabled = True
        self.extractor.return_value[0].pop("has_audio_track")
        self.assertFalse((await self.service.read(self.key, "a", include_audio=True))["success"])
        self.transcribe.assert_not_awaited()
        self.generate.assert_not_awaited()

    async def test_audio_failure_never_silently_degrades_or_replays_after_reload(self):
        self.cfg.asr.enabled = True
        self.transcribe.side_effect = TimeoutError("private upstream detail")
        first = await self.service.read(self.key, "a", include_audio=True)
        self.assertFalse(first["success"])
        self.assertNotIn("private upstream", str(first))
        self.service.handles.clear()
        self.service.store = None
        second = self.assets.add("a", "video", ["https://example.com/other.mp4"])
        self.assertFalse((await self.service.read(second, "a", include_audio=True))["success"])
        self.transcribe.assert_awaited_once()
        self.generate.assert_not_awaited()

    async def test_cancel_during_asr_preserves_unknown_and_cleans_video(self):
        self.cfg.asr.enabled = True
        entered = asyncio.Event()

        async def transcribe(audio):
            entered.set()
            await asyncio.Event().wait()

        self.transcribe.side_effect = transcribe
        task = asyncio.create_task(self.service.read(self.key, "a", include_audio=True))
        await entered.wait()
        await self.service.close()
        self.assertTrue(task.cancelled())
        self.assertFalse(any(path.exists() for path in self.created))
        self.service.closed = False
        self.assertFalse((await self.service.read(self.key, "a", include_audio=True))["success"])
        self.transcribe.assert_awaited_once()
        self.generate.assert_not_awaited()

    async def test_audio_decode_failure_happens_before_admission(self):
        self.cfg.asr.enabled = True
        self.audio_extract.side_effect = MediaError("invalid audio")
        self.assertFalse((await self.service.read(self.key, "a", include_audio=True))["success"])
        self.assertIsNone(await self.service.db("get", next(iter(self.service.handles.values()))))
        self.transcribe.assert_not_awaited()
        self.generate.assert_not_awaited()

    async def test_visual_failure_after_asr_never_repeats_audio(self):
        self.cfg.asr.enabled = True
        self.generate.side_effect = TimeoutError()
        for _ in range(2):
            self.assertFalse(
                (await self.service.read(self.key, "a", include_audio=True))["success"]
            )
        self.transcribe.assert_awaited_once()
        self.generate.assert_awaited_once()

    async def test_disabled_during_asr_does_not_return_transcript_or_start_visual(self):
        self.cfg.asr.enabled = True

        async def transcribe(audio):
            self.cfg.asr.enabled = False
            return {"success": True, "text": "private answer"}

        self.transcribe.side_effect = transcribe
        result = await self.service.read(self.key, "a", include_audio=True)
        self.assertFalse(result["success"])
        self.assertNotIn("private answer", str(result))
        self.generate.assert_not_awaited()

    async def test_visual_only_cache_cannot_satisfy_audio_or_audio_continuation(self):
        self.cfg.asr.enabled = True
        await self.service.read(self.key, "a")
        self.assertFalse((await self.service.read(self.key, "a", 1, include_audio=True))["success"])
        self.transcribe.assert_not_awaited()
        result = await self.service.read(self.key, "a", include_audio=True)
        self.assertFalse(result["cache_hit"])
        self.assertTrue(result["audio_analyzed"])
        self.transcribe.assert_awaited_once()
        self.assertEqual(self.generate.await_count, 2)

    async def test_audio_continuation_uses_combined_cached_body_only(self):
        self.cfg.asr.enabled = True
        self.generate.return_value = {"content": "x" * 6000, "finish_reason": "stop"}
        first = await self.service.read(self.key, "a", include_audio=True)
        last = await self.service.read(
            self.key, "a", first["next_analysis_offset"], include_audio=True
        )
        self.assertIn("这是一段合成转写", last["content"])
        self.assertTrue(last["cache_hit"])
        self.transcribe.assert_awaited_once()
        self.generate.assert_awaited_once()

    async def test_audio_is_scope_bound_and_parallel_requests_share_one_attempt(self):
        self.cfg.asr.enabled = True
        self.assertFalse((await self.service.read(self.key, "b", include_audio=True))["success"])
        self.fetcher.assert_not_awaited()
        values = await asyncio.gather(
            self.service.read(self.key, "a", include_audio=True),
            self.service.read(self.key, "a", include_audio=True),
        )
        self.assertTrue(all(value["success"] for value in values))
        self.transcribe.assert_awaited_once()
        self.generate.assert_awaited_once()
        self.audio_extract.assert_awaited_once()

    async def test_failed_empty_overlong_asr_not_success_and_no_visual(self):
        self.cfg.asr.enabled = True
        for value in [
            {"success": False, "text": "private"},
            {},
            {"success": True, "text": " "},
            {"text": "a" * 8001},
        ]:
            with tempfile.TemporaryDirectory() as other:
                self.service.directory = Path(other)
                self.service.store = None
                self.service.handles.clear()
                self.transcribe.return_value = value
                self.assertFalse(
                    (await self.service.read(self.key, "a", include_audio=True))["success"]
                )
        self.generate.assert_not_awaited()

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
