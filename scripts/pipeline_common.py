"""Shared paths, atomic artifacts and content-based cache validation."""
import hashlib
import json
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MEDIA_SUFFIXES = {".mp4", ".mov", ".mkv", ".avi", ".wav"}


def fingerprint(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {} if default is None else default


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def cache_matches(artifact, manifest, inputs):
    if not Path(artifact).is_file():
        return False
    saved = read_json(manifest)
    if not isinstance(saved, dict):
        return False
    return saved.get("inputs") == inputs and saved.get("artifact_sha256") == fingerprint(artifact)


def save_cache(artifact, manifest, inputs):
    save_json(manifest, {"inputs": inputs, "artifact_sha256": fingerprint(artifact)})
