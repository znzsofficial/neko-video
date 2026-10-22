"""Persistent paid-attempt admission and short result cache, scoped by chat/hash.

Write unknown before the supplier call; never erase it to retry after a reload.
No video URLs, cookies or video bodies are persisted here.
"""

import json
from pathlib import Path
import sqlite3
import time
from contextlib import closing

from .media import MediaError


class AnalysisStore:
    def __init__(self, directory, clock=time.time):
        self.root = Path(directory)
        self.clock = clock
        if self.root.is_symlink():
            raise MediaError("视频分析数据目录不安全")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / "attempts.db"
        if self.path.is_symlink():
            raise MediaError("视频分析记录不安全")
        with closing(self.connect()) as con, con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS analysis (id TEXT PRIMARY KEY, created REAL, state TEXT, result TEXT)"
            )
        self.path.chmod(0o600)

    def connect(self):
        con = sqlite3.connect(self.path, timeout=3)
        try:
            con.execute("PRAGMA max_page_count=2048")
        except BaseException:
            con.close()
            raise
        return con

    def get(self, key):
        con = self.connect()
        try:
            row = con.execute(
                "SELECT created,state,result FROM analysis WHERE id=?", (key,)
            ).fetchone()
            if row is None or row[0] <= self.clock() - 3600:
                return None
            if row[1] != "complete":
                return {"state": "unknown"}
            return {"state": "complete", "result": json.loads(row[2])}
        finally:
            con.close()

    def admit(self, key):
        con = self.connect()
        try:
            with con:
                con.execute("BEGIN IMMEDIATE")
                con.execute("DELETE FROM analysis WHERE created<=?", (self.clock() - 3600,))
                if con.execute("SELECT COUNT(*) FROM analysis").fetchone()[0] >= 128:
                    raise MediaError("视频分析尝试记录已满，未提交模型请求")
                return (
                    con.execute(
                        "INSERT OR IGNORE INTO analysis VALUES (?,?,?,?)",
                        (key, self.clock(), "unknown", ""),
                    ).rowcount
                    == 1
                )
        finally:
            con.close()

    def complete(self, key, result):
        raw = json.dumps(result, ensure_ascii=False)
        if len(raw.encode("utf-8")) > 32768:
            raise MediaError("分析结果超过缓存上限，未确认完整结果；不自动重试")
        con = self.connect()
        try:
            with con:
                changed = con.execute(
                    "UPDATE analysis SET state='complete',result=? WHERE id=? AND state='unknown'",
                    (raw, key),
                ).rowcount
                if changed != 1:
                    raise MediaError("视频分析记录状态异常，不自动重试")
        finally:
            con.close()
