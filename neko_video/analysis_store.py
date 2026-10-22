"""Persistent paid-attempt admission and short result cache, scoped by chat/hash.

Write unknown before the supplier call; never erase it to retry after a reload.
No video URLs, cookies or video bodies are persisted here.
"""

import json
import math
from pathlib import Path
import sqlite3
import time
from contextlib import closing, contextmanager

from .media import MediaError


class AnalysisStore:
    def __init__(self, directory, clock=time.time, monotonic=time.monotonic):
        self.root = Path(directory)
        self.clock = clock
        self.monotonic = monotonic
        self._wall = self._tick = None
        if self.root.is_symlink():
            raise MediaError("视频分析数据目录不安全")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / "attempts.db"
        if self.path.is_symlink():
            raise MediaError("视频分析记录不安全")
        with closing(self.connect()) as con, con:
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                "CREATE TABLE IF NOT EXISTS analysis (id TEXT PRIMARY KEY, created REAL, state TEXT, result TEXT)"
            )
            con.execute(
                "CREATE TABLE IF NOT EXISTS analysis_identity (id TEXT PRIMARY KEY, created REAL, attempt TEXT NOT NULL)"
            )
            columns = [row[1] for row in con.execute("PRAGMA table_info(analysis)")]
            if columns == ["id", "created", "state", "result", "attempt"]:
                self.migrate_five_columns(con)
            elif columns != ["id", "created", "state", "result"]:
                raise MediaError("视频分析记录结构异常，保留数据库且不请求模型")
            con.execute(
                "CREATE TABLE IF NOT EXISTS analysis_clock (id INTEGER PRIMARY KEY CHECK(id=1), wall REAL, fault TEXT NOT NULL)"
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

    @staticmethod
    def migrate_five_columns(con):
        # The short-lived five-column format broke baseline INSERT VALUES (?,?,?,?).
        # Copy identities and restore the exact four-column contract atomically.
        if con.execute(
            "SELECT 1 FROM sqlite_master WHERE tbl_name='analysis' AND type IN ('index','trigger') AND sql IS NOT NULL"
        ).fetchone():
            raise MediaError("视频分析记录有额外结构，保留数据库且不自动迁移")
        for key, created, attempt in con.execute("SELECT id,created,attempt FROM analysis"):
            existing = con.execute(
                "SELECT created,attempt FROM analysis_identity WHERE id=?", (key,)
            ).fetchone()
            if existing is not None and existing != (created, attempt):
                raise MediaError("视频分析身份映射冲突，保留数据库且不自动迁移")
            con.execute(
                "INSERT OR IGNORE INTO analysis_identity VALUES (?,?,?)", (key, created, attempt)
            )
        con.execute(
            "CREATE TABLE analysis_compat (id TEXT PRIMARY KEY, created REAL, state TEXT, result TEXT)"
        )
        con.execute("INSERT INTO analysis_compat SELECT id,created,state,result FROM analysis")
        con.execute("DROP TABLE analysis")
        con.execute("ALTER TABLE analysis_compat RENAME TO analysis")

    @staticmethod
    def number(value):
        return type(value) in {int, float} and math.isfinite(value) and value >= 0

    def guard(self, con):
        now, tick = self.clock(), self.monotonic()
        previous = con.execute("SELECT wall,fault FROM analysis_clock WHERE id=1").fetchone()
        fault = previous[1] if previous else ""
        if not self.number(now) or not self.number(tick):
            fault = "invalid clock"
        elif previous and (not self.number(previous[0]) or now < previous[0] - 5):
            fault = "wall clock rollback"
        elif self._wall is not None and abs((now - self._wall) - (tick - self._tick)) > 5:
            fault = "wall clock discontinuity"
        elif any(
            not self.number(row[0]) or row[0] > now + 5
            for row in con.execute("SELECT created FROM analysis")
        ):
            fault = "invalid or future attempt timestamp"
        if fault:
            # Commit only the fault latch; no expiry deletion or supplier admission
            # has happened in this transaction. Reload must not bypass the latch.
            con.execute(
                "INSERT OR REPLACE INTO analysis_clock VALUES (1,?,?)",
                (previous[0] if previous else None, fault),
            )
            con.commit()
            raise MediaError("视频分析时钟或记录时间异常，已保留证据并停止；需管理员离线核查")
        con.execute("INSERT OR REPLACE INTO analysis_clock VALUES (1,?,'')", (now,))
        if self._wall is None:
            self._wall, self._tick = now, tick
        return now

    @contextmanager
    def transaction(self):
        with closing(self.connect()) as con, con:
            con.execute("BEGIN IMMEDIATE")
            yield con, self.guard(con)

    def unknown(self, con, attempt, now):
        return (
            con.execute(
                "SELECT 1 FROM analysis a LEFT JOIN analysis_identity m ON a.id=m.id AND a.created=m.created WHERE a.created>? AND a.state IS NOT 'complete' AND (m.attempt=? OR m.attempt='' OR m.id IS NULL) LIMIT 1",
                (now - 3600, attempt),
            ).fetchone()
            is not None
        )

    def get(self, key, attempt=None):
        with self.transaction() as (con, now):
            if self.unknown(con, attempt or key, now):
                return {"state": "unknown"}
            row = con.execute(
                "SELECT created,state,result FROM analysis WHERE id=?", (key,)
            ).fetchone()
            if row is None or row[0] <= now - 3600:
                return None
            if row[1] != "complete":
                return {"state": "unknown"}
            return {"state": "complete", "result": json.loads(row[2])}

    def admit(self, key, attempt=None):
        with self.transaction() as (con, now):
            con.execute("DELETE FROM analysis WHERE created<=?", (now - 3600,))
            con.execute("DELETE FROM analysis_identity WHERE id NOT IN (SELECT id FROM analysis)")
            if self.unknown(con, attempt or key, now):
                return False
            if con.execute("SELECT COUNT(*) FROM analysis").fetchone()[0] >= 128:
                raise MediaError("视频分析尝试记录已满，未提交模型请求")
            inserted = (
                con.execute(
                    "INSERT OR IGNORE INTO analysis VALUES (?,?,?,?)", (key, now, "unknown", "")
                ).rowcount
                == 1
            )
            if inserted:
                con.execute(
                    "INSERT OR REPLACE INTO analysis_identity VALUES (?,?,?)",
                    (key, now, attempt or key),
                )
            return inserted

    def complete(self, key, result):
        raw = json.dumps(result, ensure_ascii=False)
        if len(raw.encode("utf-8")) > 32768:
            raise MediaError("分析结果超过缓存上限，未确认完整结果；不自动重试")
        with self.transaction() as (con, _):
            changed = con.execute(
                "UPDATE analysis SET state='complete',result=? WHERE id=? AND state='unknown'",
                (raw, key),
            ).rowcount
            if changed != 1:
                raise MediaError("视频分析记录状态异常，不自动重试")
