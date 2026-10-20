import contextlib
import importlib.util
import io
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "strict_runner", Path(__file__).resolve().parents[1] / "scripts/test_strict.py"
)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class StrictRunnerTests(unittest.TestCase):
    def run_suite(self, suite):
        with contextlib.redirect_stderr(io.StringIO()):
            return runner.run(suite)

    def test_empty_discovery_fails(self):
        self.assertFalse(self.run_suite(unittest.TestSuite()))

    def test_unexpected_skip_fails(self):
        class Skipped(unittest.TestCase):
            @unittest.skip("synthetic skip")
            def runTest(self):
                pass

        self.assertFalse(self.run_suite(unittest.TestSuite([Skipped()])))

    def test_failure_or_import_error_fails(self):
        def failure():
            raise AssertionError("synthetic failure")

        def error():
            raise ImportError("synthetic import error")

        for callback in [failure, error]:
            self.assertFalse(
                self.run_suite(unittest.TestSuite([unittest.FunctionTestCase(callback)]))
            )

    def test_real_success_passes(self):
        self.assertTrue(
            self.run_suite(unittest.TestSuite([unittest.FunctionTestCase(lambda: None)]))
        )
