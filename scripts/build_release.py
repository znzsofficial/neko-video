"""Build an allowlisted plugin ZIP without config, credentials or tests."""

import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TOP = (
    "plugin.py",
    "__init__.py",
    "_manifest.json",
    "README.md",
    "LICENSE",
    "NOTICE.md",
    "SECURITY.md",
    "CHANGELOG.md",
)


def build(output=None):
    version = json.loads((ROOT / "_manifest.json").read_text(encoding="utf-8"))["version"]
    out = Path(output) if output is not None else ROOT / "dist"
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"neko-video-{version}.zip"
    files = [ROOT / name for name in TOP] + sorted((ROOT / "neko_video").glob("*.py"))
    if any(not f.is_file() or f.is_symlink() for f in files):
        raise ValueError("Release input missing or symlinked")
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, arcname="neko-video/" + path.relative_to(ROOT).as_posix())
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    (out / "checksums.txt").write_text(digest + "  " + target.name + "\n", encoding="utf-8")
    return target, digest


if __name__ == "__main__":
    path, digest = build()
    print(f"{path.name} SHA256 {digest}")
