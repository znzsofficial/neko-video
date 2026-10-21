"""Bounded local MP4 probing/frame extraction; no SDK, shell or network input.

Protocol/format limits are not an OS filesystem sandbox. Only plugin-owned
private spool files are accepted by the caller, never a model-supplied path.
"""

import asyncio
import io
import json
import math
from pathlib import Path
import shutil
import sys
import warnings

from PIL import Image

from .media import MediaError

MAX_FRAME_BYTES = 180 * 1024
MAX_PIXELS = 16_777_216
FORMAT = "mov"


async def command(args, max_bytes, timeout):
    invocation = (
        [sys.executable, "-I", str(Path(__file__).with_name("media_process.py")), *args]
        if sys.platform.startswith("linux")
        else args
    )
    creation = asyncio.create_task(
        asyncio.create_subprocess_exec(
            *invocation,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    )
    try:
        process = await asyncio.shield(creation)
    except asyncio.CancelledError:
        process = await creation
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        await process.communicate()
        raise

    async def read(stream, limit, reject):
        value = bytearray()
        while chunk := await stream.read(65536):
            if len(value) + len(chunk) > limit:
                if reject:
                    raise MediaError("视频处理输出超过安全上限")
                continue
            value.extend(chunk)
        return bytes(value)

    tasks = [
        asyncio.create_task(read(process.stdout, max_bytes, True)),
        asyncio.create_task(read(process.stderr, 16384, False)),
    ]
    try:
        async with asyncio.timeout(timeout):
            output, _ = await asyncio.gather(*tasks)
            await process.wait()
        if process.returncode:
            raise MediaError("视频解码失败或格式不支持，未取得完整采样画面")
        return output
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await process.wait()


def executable(name):
    binary = shutil.which(name)
    if not binary:
        raise MediaError(f"服务器缺少{name}，视频分析未开始")
    return binary


def metadata(body, max_seconds):
    try:
        data = json.loads(body)
        duration = float(data.get("format", {}).get("duration", "nan"))
        streams = data.get("streams", [])
        video = next(s for s in streams if s.get("codec_type") == "video")
        width, height = video.get("width"), video.get("height")
        if (
            not math.isfinite(duration)
            or not 0 < duration <= max_seconds
            or type(width) is not int
            or type(height) is not int
            or not 0 < width <= 8192
            or not 0 < height <= 8192
            or width * height > MAX_PIXELS
            or len(streams) > 16
            or video.get("disposition", {}).get("attached_pic", 0)
        ):
            raise ValueError()
        return {
            "duration_seconds": duration,
            "width": width,
            "height": height,
            "audio_analyzed": False,
            "has_audio_track": any(s.get("codec_type") == "audio" for s in streams),
        }
    except (ValueError, TypeError, AttributeError, StopIteration, KeyError):
        raise MediaError("视频时长、尺寸或流信息无效/超过分析上限，未截断冒充完整视频") from None


def times(duration, count):
    # Bin centers avoid promising precise scene-change coverage or sampling the
    # potentially empty very last frame. These are requested seek positions.
    count = min(count, max(1, math.ceil(duration)))
    return [round(duration * (i + 0.5) / count, 3) for i in range(count)]


def compact(raw):
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            with Image.open(io.BytesIO(raw)) as image:
                if image.format != "JPEG" or image.width * image.height > 960 * 960:
                    raise ValueError()
                image.load()
                image = image.convert("RGB")
                for _ in range(4):
                    out = io.BytesIO()
                    image.save(out, format="JPEG", quality=70)
                    if out.tell() <= MAX_FRAME_BYTES:
                        return out.getvalue()
                    image.thumbnail((max(1, image.width * 3 // 4), max(1, image.height * 3 // 4)))
        except (OSError, ValueError, Image.DecompressionBombWarning, Image.DecompressionBombError):
            pass
    raise MediaError("视频帧无效或超过传输上限")


async def extract(path, max_seconds=120, count=6):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise MediaError("视频缓存不可读取")
    ffmpeg, ffprobe = executable("ffmpeg"), executable("ffprobe")
    protocols = ["-protocol_whitelist", "file,pipe", "-format_whitelist", FORMAT]
    raw = await command(
        [
            ffprobe,
            "-v",
            "error",
            *protocols,
            "-show_entries",
            "format=duration:stream=codec_type,width,height:stream_disposition=attached_pic",
            "-of",
            "json",
            str(path),
        ],
        65536,
        10,
    )
    info = metadata(raw, max_seconds)
    positions = times(info["duration_seconds"], count)
    frames = []
    for position in positions:
        raw = await command(
            [
                ffmpeg,
                "-nostdin",
                "-v",
                "error",
                "-threads",
                "1",
                *protocols,
                "-ss",
                str(position),
                "-i",
                str(path),
                "-map",
                "0:v:0",
                "-an",
                "-sn",
                "-dn",
                "-frames:v",
                "1",
                "-filter_threads",
                "1",
                "-vf",
                "scale=w='min(960,iw)':h='min(960,ih)':force_original_aspect_ratio=decrease",
                "-c:v",
                "mjpeg",
                "-threads",
                "1",
                "-q:v",
                "5",
                "-f",
                "image2pipe",
                "pipe:1",
            ],
            1024 * 1024,
            10,
        )
        frames.append(await asyncio.to_thread(compact, raw))
    return info, positions, frames
