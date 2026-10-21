"""Bounded first-track extraction from owned video; no SDK, network or disk audio."""

import io
from pathlib import Path
import wave

from .media import MediaError
from .video_frames import command, executable, FORMAT

RATE = 16000
MAX_AUDIO_BYTES = 120 * RATE * 2


async def extract_audio(path, max_seconds=120):
    path = Path(path)
    if type(max_seconds) is not int or not 1 <= max_seconds <= 120:
        raise MediaError("音轨时长上限无效")
    if path.is_symlink() or not path.is_file():
        raise MediaError("视频音轨缓存不可读取")
    # Read one extra second, rejecting rather than clipping an overlong track.
    raw = await command(
        [
            executable("ffmpeg"),
            "-nostdin",
            "-v",
            "error",
            "-threads",
            "1",
            "-protocol_whitelist",
            "file,pipe",
            "-format_whitelist",
            FORMAT,
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-vn",
            "-sn",
            "-dn",
            "-t",
            str(max_seconds + 1),
            "-ac",
            "1",
            "-ar",
            str(RATE),
            "-c:a",
            "pcm_s16le",
            "-threads",
            "1",
            "-f",
            "s16le",
            "pipe:1",
        ],
        min(MAX_AUDIO_BYTES, max_seconds * RATE * 2),
        15,
    )
    if not raw or len(raw) % 2 or len(raw) > max_seconds * RATE * 2:
        raise MediaError("未取得有效完整PCM音轨，未提交转写")
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(RATE)
        audio.writeframes(raw)
    return output.getvalue(), len(raw) / (RATE * 2)
