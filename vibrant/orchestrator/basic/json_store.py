"""Shared JSON persistence helpers for orchestrator stores."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import TypeVar, cast

from vibrant.type_defs import JSONValue, is_json_value

JSONDocument = TypeVar("JSONDocument", bound=JSONValue)


def read_json(path: Path, *, default: JSONDocument) -> JSONDocument:
    """Read JSON from disk, returning ``default`` when the file is absent."""

    if not path.exists():
        return default
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return default
    payload = json.loads(text)
    assert is_json_value(payload), f"JSON file {path} must contain JSON-compatible data"
    return cast(JSONDocument, payload)


def write_json(path: Path, payload: JSONValue) -> None:
    """Atomically write JSON to disk."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_path = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise
