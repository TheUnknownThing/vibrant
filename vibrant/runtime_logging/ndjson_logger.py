"""NDJSON loggers for native and canonical provider event streams."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


class NdjsonLogger:
    """Append-only newline-delimited JSON logger.

    Each line is written as:
    ``{"timestamp": "...", "event": "...", "data": {...}}``
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(
        self,
        event: str,
        data: Mapping[str, Any] | None = None,
        *,
        timestamp: str | None = None,
    ) -> None:
        payload = {
            "timestamp": timestamp or _timestamp_now(),
            "event": event,
            "data": dict(data or {}),
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
            handle.flush()

    def write(self, event: dict[str, Any] | str, data: Mapping[str, Any] | None = None) -> None:
        """Compatibility wrapper around :meth:`log`."""
        if isinstance(event, dict):
            self.log(
                str(event.get("event") or event.get("type") or "event"),
                event.get("data") if isinstance(event.get("data"), Mapping) else event,
                timestamp=event.get("timestamp") if isinstance(event.get("timestamp"), str) else None,
            )
            return
        self.log(event, data)


class NativeLogger(NdjsonLogger):
    """Logger for raw provider diagnostics and JSON-RPC traffic."""

    def log_jsonrpc(self, event: str, message: Mapping[str, Any]) -> None:
        self.log(event, message)

    def log_stderr(self, line: str) -> None:
        self.log("stderr.line", {"line": line})


class CanonicalLogger(NdjsonLogger):
    """Logger for normalized events consumed by Vibrant."""

    def log_canonical(self, event: str, data: Mapping[str, Any] | None = None, *, timestamp: str | None = None) -> None:
        self.log(event, data, timestamp=timestamp)


def _timestamp_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
