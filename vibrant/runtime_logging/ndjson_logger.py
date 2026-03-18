"""NDJSON loggers for native and canonical provider event streams."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from vibrant.type_defs import JSONMapping, JSONObject, JSONValue, is_json_mapping, is_json_object


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
        data: JSONMapping | None = None,
        *,
        timestamp: str | None = None,
    ) -> None:
        payload: JSONObject = {
            "timestamp": timestamp or _timestamp_now(),
            "event": event,
            "data": dict(data or {}),
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
            handle.flush()

    def write(self, event: JSONObject | str, data: JSONMapping | None = None) -> None:
        """Compatibility wrapper around :meth:`log`."""
        if is_json_object(event):
            embedded_data = event.get("data")
            timestamp = event.get("timestamp")
            self.log(
                str(event.get("event") or event.get("type") or "event"),
                embedded_data if is_json_mapping(embedded_data) else event,
                timestamp=timestamp if isinstance(timestamp, str) else None,
            )
            return
        self.log(event, data)


class NativeLogger(NdjsonLogger):
    """Logger for raw provider diagnostics and JSON-RPC traffic."""

    def log_jsonrpc(self, event: str, message: JSONMapping) -> None:
        self.log(event, message)

    def log_stderr(self, line: str) -> None:
        self.log("stderr.line", {"line": line})


class CanonicalLogger(NdjsonLogger):
    """Logger for normalized events consumed by Vibrant."""

    def log_canonical(self, event: str, data: JSONMapping | None = None, *, timestamp: str | None = None) -> None:
        self.log(event, data, timestamp=timestamp)


def _timestamp_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
