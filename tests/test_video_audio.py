import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
import wave

from neko_video.media import MediaError
from neko_video.video_audio import extract_audio, MAX_AUDIO_BYTES


class AudioTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "owned-video.mp4"
        self.path.write_bytes(b"synthetic video")

    async def test_pcm_to_valid_mono_16k_wav_and_restricted_command(self):
        with (
            patch("neko_video.video_audio.executable", return_value="ffmpeg"),
            patch("neko_video.video_audio.command", AsyncMock(return_value=b"\0\0" * 16000)) as run,
        ):
            data, seconds = await extract_audio(self.path)
        self.assertEqual(seconds, 1)
        with wave.open(io.BytesIO(data), "rb") as audio:
            self.assertEqual(
                (
                    audio.getnchannels(),
                    audio.getsampwidth(),
                    audio.getframerate(),
                    audio.getnframes(),
                ),
                (1, 2, 16000, 16000),
            )
        args, limit, timeout = run.call_args.args
        self.assertEqual(limit, MAX_AUDIO_BYTES)
        self.assertEqual(timeout, 15)
        self.assertEqual(args[args.index("-protocol_whitelist") + 1], "file,pipe")
        self.assertEqual(args[args.index("-format_whitelist") + 1], "mov")
        self.assertEqual(args[args.index("-map") + 1], "0:a:0")
        self.assertEqual(args[args.index("-t") + 1], "121")
        self.assertEqual(args[-1], "pipe:1")
        self.assertEqual(list(Path(self.directory.name).iterdir()), [self.path])

    async def test_empty_odd_or_overlong_audio_never_becomes_valid_wav(self):
        for raw in [b"", b"x", b"\0" * (MAX_AUDIO_BYTES + 2)]:
            with (
                patch("neko_video.video_audio.executable", return_value="ffmpeg"),
                patch("neko_video.video_audio.command", AsyncMock(return_value=raw)),
            ):
                with self.assertRaises(MediaError):
                    await extract_audio(self.path)
        with (
            patch("neko_video.video_audio.executable", return_value="ffmpeg"),
            patch(
                "neko_video.video_audio.command",
                AsyncMock(side_effect=MediaError("output over limit")),
            ),
        ):
            with self.assertRaises(MediaError):
                await extract_audio(self.path, 1)

    async def test_missing_path_does_not_start_decoder(self):
        self.path.unlink()
        with patch("neko_video.video_audio.command", AsyncMock()) as run:
            with self.assertRaises(MediaError):
                await extract_audio(self.path)
            run.assert_not_awaited()
