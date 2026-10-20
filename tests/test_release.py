import importlib.util
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("neko_release", ROOT / "scripts/build_release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
verify_spec = importlib.util.spec_from_file_location(
    "neko_verify", ROOT / "scripts/verify_release.py"
)
verifier = importlib.util.module_from_spec(verify_spec)
verify_spec.loader.exec_module(verifier)


class ReleaseTests(unittest.TestCase):
    def rewrite_archive(self, archive, *, remove="", extra=""):
        with zipfile.ZipFile(archive) as bundle:
            entries = {name: bundle.read(name) for name in bundle.namelist() if name != remove}
        if extra:
            entries[extra] = b"synthetic fixture"
        with zipfile.ZipFile(archive, "w") as bundle:
            for name, data in entries.items():
                bundle.writestr(name, data)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        (archive.parent / "checksums.txt").write_text(
            f"{digest}  {archive.name}\n", encoding="utf-8"
        )

    def test_missing_packaged_module_cannot_fall_back_to_editable_source(self):
        with tempfile.TemporaryDirectory() as directory:
            archive, _ = release.build(directory)
            self.rewrite_archive(archive, remove="neko-video/neko_video/media.py")
            with self.assertRaises(RuntimeError):
                verifier.verify(archive)

    def test_matching_checksum_does_not_allow_runtime_or_private_members(self):
        for name in ["config.toml", "Key", "data/state.json", "spool/video.mp4", "../escape"]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                archive, _ = release.build(directory)
                self.rewrite_archive(archive, extra="neko-video/" + name)
                with self.assertRaisesRegex(ValueError, "Non-allowlisted"):
                    verifier.verify(archive)

    def test_final_archive_real_sdk_discovery_in_isolated_process(self):
        with tempfile.TemporaryDirectory() as directory:
            archive, digest = release.build(directory)
            self.assertEqual(verifier.verify(archive), digest)

    def test_archive_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            archive, _ = release.build(directory)
            with zipfile.ZipFile(archive, "a") as bundle:
                bundle.writestr("neko-video/config.toml", "sensitive = true")
            with self.assertRaisesRegex(ValueError, "checksum"):
                verifier.verify(archive)

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
