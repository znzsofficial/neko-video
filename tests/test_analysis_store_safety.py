from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import types
import unittest
from unittest.mock import patch

from neko_video.analysis_store import AnalysisStore
from neko_video.media import MediaError


class StoreSafetyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.wall = 10000.0
        self.tick = 500.0
        self.store = self.open_store()

    def open_store(self):
        return AnalysisStore(
            self.directory.name, clock=lambda: self.wall, monotonic=lambda: self.tick
        )

    def rows(self, table="analysis"):
        with closing(sqlite3.connect(self.store.path)) as con:
            return con.execute(f"SELECT * FROM {table}").fetchall()

    def test_real_baseline_source_upgrade_then_rollback_insert_contract(self):
        source = (Path(__file__).parent / "fixtures/analysis_store_b4e36f4.py.txt").read_text(
            encoding="utf-8"
        )
        raw = source.encode("utf-8")
        # Exact Git blob of b4e36f4:neko_video/analysis_store.py, not a rewritten stand-in.
        self.assertEqual(
            hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest(),
            "28060c5c1c8b537d4d2d8ca2d8b05823994e6941",
        )
        legacy = types.ModuleType("neko_video._baseline_store")
        legacy.__package__ = "neko_video"
        exec(compile(source, "analysis_store_b4e36f4.py", "exec"), legacy.__dict__)
        baseline = legacy.AnalysisStore(self.directory.name, clock=lambda: self.wall)
        self.assertTrue(baseline.admit("old"))
        baseline.complete("old", {"content": "retained"})
        upgraded = self.open_store()
        self.assertTrue(upgraded.admit("new", "stable-identity"))
        upgraded.complete("new", {"content": "new retained"})
        rolled_back = legacy.AnalysisStore(self.directory.name, clock=lambda: self.wall)
        self.assertTrue(rolled_back.admit("rollback-four-value-insert"))
        self.assertEqual(rolled_back.get("old")["result"]["content"], "retained")
        self.assertEqual(rolled_back.get("new")["result"]["content"], "new retained")
        # Missing identity on a newly inserted baseline row becomes a legacy global barrier.
        self.assertEqual(upgraded.get("new", "stable-identity"), {"state": "unknown"})

    def five_columns(self):
        with closing(sqlite3.connect(self.store.path)) as con, con:
            con.execute("DROP TABLE analysis")
            con.execute(
                "CREATE TABLE analysis (id TEXT PRIMARY KEY, created REAL, state TEXT, result TEXT, attempt TEXT NOT NULL DEFAULT '')"
            )
            con.executemany(
                "INSERT INTO analysis VALUES (?,?,?,?,?)",
                [
                    ("unknown", self.wall, "unknown", "", "identity-a"),
                    ("complete", self.wall, "complete", '{"content":"retained"}', "identity-b"),
                    ("legacy", self.wall, "unknown", "", ""),
                ],
            )

    def test_five_column_transition_migrates_without_losing_rows_or_identities(self):
        self.five_columns()
        original = self.rows()
        upgraded = self.open_store()
        self.assertEqual(self.rows(), [row[:4] for row in original])
        self.assertEqual(
            sorted(self.rows("analysis_identity")), sorted((r[0], r[1], r[4]) for r in original)
        )
        with closing(sqlite3.connect(upgraded.path)) as con, con:
            con.execute(
                "INSERT INTO analysis VALUES (?,?,?,?)", ("rollback", self.wall, "unknown", "")
            )
        self.assertEqual(upgraded.get("complete", "identity-b"), {"state": "unknown"})

    def test_five_column_migration_rolls_back_after_drop_and_rename_failure(self):
        self.five_columns()
        original = self.rows()
        migrate = AnalysisStore.migrate_five_columns

        def fail_after_ddl(con):
            migrate(con)
            raise sqlite3.OperationalError("synthetic failure after rename")

        with patch.object(AnalysisStore, "migrate_five_columns", side_effect=fail_after_ddl):
            with self.assertRaises(sqlite3.OperationalError):
                self.open_store()
        self.assertEqual(self.rows(), original)
        self.assertEqual(self.rows("analysis_identity"), [])
        self.open_store()
        self.assertEqual(self.rows(), [row[:4] for row in original])

    def test_wall_forward_jump_does_not_expire_unknown_and_is_latched_across_reload(self):
        self.assertTrue(self.store.admit("a", "identity"))
        original = self.rows()
        self.wall += 3601
        self.tick += 1
        with self.assertRaises(MediaError):
            self.store.admit("b", "identity")
        self.assertEqual(self.rows(), original)
        self.wall -= 3600
        with self.assertRaises(MediaError):
            self.open_store().get("a", "identity")

    def test_wall_rollback_is_detected_in_process_and_after_reload(self):
        for reload in [False, True]:
            with self.subTest(reload=reload), tempfile.TemporaryDirectory() as directory:
                store = AnalysisStore(
                    directory, clock=lambda: self.wall, monotonic=lambda: self.tick
                )
                store.admit("a", "identity")
                self.wall -= 100
                self.tick += 1
                if reload:
                    store = AnalysisStore(
                        directory, clock=lambda: self.wall, monotonic=lambda: self.tick
                    )
                with self.assertRaises(MediaError):
                    store.admit("b", "identity")

    def test_invalid_or_future_created_fails_closed_without_deleting_evidence(self):
        for created in [None, "not-a-number", float("nan"), float("inf"), -1, self.wall + 10**9]:
            with self.subTest(created=created), tempfile.TemporaryDirectory() as directory:
                store = AnalysisStore(
                    directory, clock=lambda: self.wall, monotonic=lambda: self.tick
                )
                with closing(sqlite3.connect(store.path)) as con, con:
                    con.execute(
                        "INSERT INTO analysis VALUES (?,?,?,?)", ("bad", created, "unknown", "")
                    )
                for action in [
                    lambda: store.get("different", "identity"),
                    lambda: store.admit("new", "identity"),
                ]:
                    with self.assertRaises(MediaError):
                        action()
                with closing(sqlite3.connect(store.path)) as con:
                    self.assertEqual(con.execute("SELECT COUNT(*) FROM analysis").fetchone()[0], 1)
                    self.assertEqual(
                        con.execute("SELECT COUNT(*) FROM analysis_identity").fetchone()[0], 0
                    )

    def test_normal_elapsed_time_expires_and_minor_clock_skew_is_tolerated(self):
        self.store.admit("a", "identity")
        self.wall += 3601
        self.tick += 3600
        self.assertTrue(self.store.admit("b", "identity"))
        self.assertEqual([row[0] for row in self.rows()], ["b"])

    def test_forward_jump_across_restart_is_not_claimed_detectable(self):
        self.store.admit("a", "identity")
        self.wall += 3601
        # No monotonic baseline survives restart: indistinguishable from legitimate downtime.
        self.assertTrue(self.open_store().admit("b", "identity"))

    def test_primary_row_and_identity_mapping_are_one_atomic_admission(self):
        with closing(sqlite3.connect(self.store.path)) as con, con:
            con.execute(
                "CREATE TRIGGER reject_identity BEFORE INSERT ON analysis_identity BEGIN SELECT RAISE(ABORT,'synthetic mapping failure'); END"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.admit("a", "identity")
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.rows("analysis_identity"), [])

    def test_cumulative_small_forward_steps_are_not_allowed_to_expire_unknown(self):
        self.store.admit("a", "identity")
        self.wall += 4
        self.store.get("a", "identity")
        self.wall += 4
        with self.assertRaises(MediaError):
            self.store.admit("b", "identity")
        self.assertEqual([row[0] for row in self.rows()], ["a"])
