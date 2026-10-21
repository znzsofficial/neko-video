"""Linux real-binary smoke with synthetic video; no SDK/network/model/QQ needed."""

import asyncio
import io
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def main():
    from neko_video.video_frames import extract
    from neko_video.video_audio import extract_audio
    from neko_video.media import MediaError

    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    assert ffmpeg and ffprobe, "Real ffmpeg and ffprobe required; do not skip this gate"
    with tempfile.TemporaryDirectory(prefix="neko-video-synthetic-") as directory:
        path = Path(directory) / "fixture.mp4"
        subprocess.run(
            [
                ffmpeg,
                "-nostdin",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=320x240:rate=4",
                "-t",
                "3",
                "-c:v",
                "mpeg4",
                "-threads",
                "1",
                str(path),
            ],
            check=True,
            timeout=15,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        info, positions, images = await extract(path, 120, 6)
        assert info["duration_seconds"] == 3
        assert len(images) == len(positions) == 3
        assert all(image.startswith(b"\xff\xd8") and len(image) <= 180 * 1024 for image in images)
        assert all(0 < value < 3 for value in positions)
        assert info["has_audio_track"] is False
        audible = Path(directory) / "audible.mp4"
        subprocess.run(
            [
                ffmpeg,
                "-nostdin",
                "-v",
                "error",
                "-i",
                str(path),
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=16000",
                "-t",
                "3",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-threads",
                "1",
                str(audible),
            ],
            check=True,
            timeout=15,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        with_audio, _, _ = await extract(audible, 120, 2)
        assert with_audio["has_audio_track"] is True
        wav, seconds = await extract_audio(audible, 120)
        assert 2.9 <= seconds <= 3.2 and len(wav) < 120 * 16000 * 2 + 44
        with wave.open(io.BytesIO(wav), "rb") as audio:
            assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) == (
                1,
                2,
                16000,
            )
            assert audio.getnframes() / 16000 == seconds
        try:
            await extract_audio(audible, 1)
        except MediaError:
            pass
        else:
            raise AssertionError("Overlong track must be rejected, not clipped")
        print(
            "REAL_FFPROBE_FFMPEG_SYNTHETIC_SMOKE_OK: JPEG frames, valid WAV, no-track and audio overlimit; no models or sends"
        )


if __name__ == "__main__":
    asyncio.run(main())
