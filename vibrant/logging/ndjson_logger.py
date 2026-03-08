"""NDJSON event logger."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class NdjsonLogger:
    """Append-only newline-delimited JSON logger."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

