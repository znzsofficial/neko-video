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
    sys.exit(0 if run(suite) else 1)
