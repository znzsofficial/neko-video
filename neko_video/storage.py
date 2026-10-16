"""Private, bounded and atomic credential storage. Invalid files fail closed."""

import json
import os
from pathlib import Path
import tempfile


class StorageError(ValueError):
    pass


def read_json(path: Path, limit=65536):
    try:
        if not path.exists():
            return {}
        if path.is_symlink() or path.stat().st_size > limit:
            raise StorageError("凭据文件不安全或过大，已保留原文件")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise StorageError("凭据文件格式错误，已保留原文件")
        return value
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise StorageError("凭据文件无法读取，已保留原文件") from None


def write_json(path: Path, data):
    # Refuse to overwrite a corrupt pre-existing credential file.
    read_json(path)
    body = json.dumps(data, ensure_ascii=False, indent=2)
    if len(body.encode("utf-8")) > 65536:
        raise StorageError("凭据文件超过上限")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        os.chmod(name, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def cookie_dict(text):
    result = {}
    if not isinstance(text, str) or len(text) > 65536:
        return result
    for line in text.strip().splitlines():
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_") :]
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) >= 7:
            result[fields[5].strip()] = fields[6].strip()
        else:
            for part in line.split(";"):
                if "=" in part:
                    key, value = part.strip().split("=", 1)
                    if key and "\r" not in value and "\n" not in value:
                        result[key] = value
    return result
