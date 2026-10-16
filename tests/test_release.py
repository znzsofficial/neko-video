import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("neko_release", ROOT / "scripts/build_release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def test_release_is_explicitly_allowlisted_and_contains_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            archive, digest = release.build(directory)
            self.assertEqual(len(digest), 64)
            with zipfile.ZipFile(archive) as z:
                names = set(z.namelist())
                self.assertIn("neko-video/LICENSE", names)
                self.assertIn("neko-video/NOTICE.md", names)
                self.assertIn("neko-video/neko_video/plugin.py", names)
                self.assertIn("neko-video/_manifest.json", names)
                self.assertFalse(
                    any(
                        name.startswith("neko-video/tests/")
                        or name.endswith("config.toml")
                        or "secret" in name
                        or "credential.json" in name
                        for name in names
                    )
                )
                manifest = json.loads(z.read("neko-video/_manifest.json"))
                self.assertEqual(manifest["id"], "neko.video")

    def test_domain_layers_do_not_import_sdk(self):
        import ast

        for path in (ROOT / "neko_video").glob("*.py"):
            if path.name in {"plugin.py", "config.py"}:
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom):
                    self.assertNotIn("maibot_sdk", node.module or "", str(path))
