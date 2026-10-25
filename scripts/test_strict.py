"""Fail closed on empty discovery, skips, failures and errors."""

from pathlib import Path
import sys
import unittest


def run(suite):
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return bool(result.testsRun and result.wasSuccessful() and not result.skipped)


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    suite = unittest.defaultTestLoader.discover(str(root / "tests"), top_level_dir=str(root))

    def collected(group):
        for test in group:
            if isinstance(test, unittest.TestSuite):
                yield from collected(test)
            else:
                yield test.id().rsplit(".", 2)[0]

    groups = set(collected(suite))
    required = {
        "tests.test_delivery",
        "tests.test_analysis_store_safety",
        "tests.test_video_spool",
        "tests.test_video_audio",
        "tests.test_release",
        "tests.test_adapter",
        "tests.test_chat_media",
        "tests.test_bilibili_text",
    }
    if not required <= groups:
        raise SystemExit(
            "Required media/delivery/package test groups missing: "
            + ", ".join(sorted(required - groups))
        )
    sys.exit(0 if run(suite) else 1)
