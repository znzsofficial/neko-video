"""Linux real-binary smoke with synthetic video; no SDK/network/model/QQ needed."""

import asyncio
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def main():
    from neko_video.video_frames import extract

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
        print("REAL_FFPROBE_FFMPEG_SYNTHETIC_SMOKE_OK: 3 sampled JPEG frames; no models or sends")


if __name__ == "__main__":
    asyncio.run(main())
