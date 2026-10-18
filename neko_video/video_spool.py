"""Public video streams to private local files, never binary RPC or remote URLs."""

import asyncio
from contextlib import aclosing
import os
from pathlib import Path
import re
import shutil
import time

from .media import download_chunks, MediaError, validate_original

TTL = 3600  # Keep sent/uncertain files long enough for asynchronous platform reads.
CACHE_BYTES = 1024 * 1024 * 1024
FREE_BYTES = 256 * 1024 * 1024
OWN_FILE = re.compile(r"video-[a-f0-9]{32}\.(?:part|mp4)")


class VideoSpool:
    def __init__(self, directory, *, clock=time.time):
        self.directory = Path(directory).absolute() / "video-spool"
        self.clock = clock
        self.lock = asyncio.Lock()
        self.active = set()

    def _files(self):
        if self.directory.is_symlink():
            raise MediaError("视频缓存目录不安全")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        return [
            p
            for p in self.directory.iterdir()
            if OWN_FILE.fullmatch(p.name) and not p.is_symlink() and p.is_file()
        ]

    def _clean(self):
        for p in self._files():
            if p not in self.active and self.clock() - p.stat().st_mtime >= TTL:
                p.unlink()

    async def clean(self):
        async with self.lock:
            work = asyncio.create_task(asyncio.to_thread(self._clean))
            try:
                await asyncio.shield(work)
            except asyncio.CancelledError:
                await asyncio.gather(work, return_exceptions=True)
                raise

    def _create(self):
        import uuid

        self._clean()
        files = self._files()
        if len(files) >= 32 or sum(p.stat().st_size for p in files) >= CACHE_BYTES:
            raise MediaError("视频缓存暂满，请稍后再试")
        # UUID plus exclusive create; model/caller never supplies a local path.
        path = self.directory / ("video-" + uuid.uuid4().hex + ".part")
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        self.active.add(path)
        return path, fd

    def _write(self, handle, block, total, limit):
        if limit and total + len(block) > limit:
            raise MediaError("视频超过配置的大小上限")
        if shutil.disk_usage(self.directory).free - len(block) < FREE_BYTES:
            raise MediaError("磁盘可用空间不足，停止下载视频")
        if sum(p.stat().st_size for p in self._files()) + len(block) > CACHE_BYTES:
            raise MediaError("视频缓存暂满，请稍后再试")
        handle.write(block)
        handle.flush()

    async def download(self, client, url, max_bytes):
        async with self.lock:
            # Creation is bounded and synchronous so cancellation cannot orphan
            # an fd returned by a still-running executor call.
            path, fd = self._create()
        total, header, complete = 0, bytearray(), False
        try:
            with os.fdopen(fd, "wb", buffering=0) as handle:
                async with aclosing(download_chunks(client, url)) as chunks:
                    async for block in chunks:
                        if len(header) < 16:
                            header.extend(block[: 16 - len(header)])
                            if len(header) == 16:
                                validate_original(bytes(header), "video")
                        # Hold the shared quota lock until a cancellation-safe
                        # disk write finishes. No file removal races the writer.
                        async with self.lock:
                            write = asyncio.create_task(
                                asyncio.to_thread(self._write, handle, block, total, max_bytes)
                            )
                            try:
                                await asyncio.shield(write)
                            except asyncio.CancelledError:
                                await asyncio.gather(write, return_exceptions=True)
                                raise
                        total += len(block)
                validate_original(bytes(header), "video")
            async with self.lock:
                target = path.with_suffix(".mp4")
                path.replace(target)
                complete = True
            return target
        finally:
            async with self.lock:
                self.active.discard(path)
                if not complete:
                    path.unlink(missing_ok=True)
