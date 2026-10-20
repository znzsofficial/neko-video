"""Verify the final ZIP and import only its extracted code with the real SDK."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

SMOKE = r"""
import importlib
import importlib.util
import pathlib
import pkgutil
import sys
import types
from maibot_sdk.components import collect_components

root = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
package = types.ModuleType('packed_neko_video')
package.__path__ = [str(root)]
sys.modules[package.__name__] = package
entry = importlib.import_module(package.__name__ + '.plugin')
plugin = entry.create_plugin()
assert not importlib.import_module('packed_neko_video.neko_video.config').Config().plugin.enabled
tools = [item for item in collect_components(plugin) if item['type'] == 'TOOL']
assert len(tools) == 4
assert all(item['name'].startswith('neko_video_') for item in tools)
# Also exercise standalone discovery and eagerly import every packaged module.
standalone = importlib.import_module('plugin')
assert len(collect_components(standalone.create_plugin())) == len(collect_components(plugin))
domain = importlib.import_module('neko_video')
for item in pkgutil.iter_modules(domain.__path__):
    importlib.import_module('neko_video.' + item.name)
for name, module in list(sys.modules.items()):
    if name == 'plugin' or name.startswith(('neko_video', 'packed_neko_video.')):
        file = getattr(module, '__file__', None)
        assert file and pathlib.Path(file).resolve().is_relative_to(root), (name, file)
print('Packed SDK discovery/import OK: 4 tools; all plugin modules from ZIP')
"""


def verify(archive):
    archive = Path(archive).resolve()
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    expected = (archive.parent / "checksums.txt").read_text(encoding="utf-8")
    if expected != f"{digest}  {archive.name}\n":
        raise ValueError("Archive checksum mismatch")
    with tempfile.TemporaryDirectory() as directory:
        with zipfile.ZipFile(archive) as bundle:
            names = bundle.namelist()
            top = {
                "plugin.py",
                "__init__.py",
                "_manifest.json",
                "README.md",
                "LICENSE",
                "NOTICE.md",
                "SECURITY.md",
                "CHANGELOG.md",
            }
            if len(names) != len(set(names)):
                raise ValueError("Duplicate archive members")
            for name in names:
                parts = name.split("/")
                allowed = (
                    len(parts) == 2
                    and parts[1] in top
                    or len(parts) == 3
                    and parts[1] == "neko_video"
                    and parts[2].endswith(".py")
                    and parts[2][:-3].isidentifier()
                )
                if parts[0] != "neko-video" or not allowed:
                    raise ValueError(f"Non-allowlisted archive member: {name}")
            if not {f"neko-video/{name}" for name in top}.issubset(names):
                raise ValueError("Missing required archive members")
            manifest = json.loads(bundle.read("neko-video/_manifest.json"))
            if manifest["id"] != "neko.video":
                raise ValueError("Wrong plugin ID")
            bundle.extractall(directory)
        completed = subprocess.run(
            [sys.executable, "-I", "-X", "utf8", "-c", SMOKE, str(Path(directory) / "neko-video")],
            cwd=directory,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        if completed.returncode:
            raise RuntimeError(completed.stdout + completed.stderr)
        print(completed.stdout.strip())
    return digest


if __name__ == "__main__":
    verify(sys.argv[1])
