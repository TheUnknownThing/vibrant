"""Tests for native and canonical NDJSON loggers."""

from __future__ import annotations

import json

from vibrant.logging.ndjson_logger import CanonicalLogger, NativeLogger, NdjsonLogger


def _read_lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class TestNdjsonLogger:
    def test_log_10_events_creates_10_valid_json_lines(self, tmp_path):
        path = tmp_path / "events.ndjson"
        logger = NdjsonLogger(path)

        for index in range(10):
            logger.log("event.created", {"index": index})

        lines = _read_lines(path)
        assert len(lines) == 10
        for index, line in enumerate(lines):
            assert set(line) == {"timestamp", "event", "data"}
            assert line["event"] == "event.created"
            assert line["data"]["index"] == index

    def test_native_log_captures_raw_jsonrpc_and_stderr(self, tmp_path):
        path = tmp_path / "native.ndjson"
        logger = NativeLogger(path)

        logger.log_jsonrpc("jsonrpc.request.sent", {"id": 1, "method": "initialize"})
        logger.log_stderr("oops")

        lines = _read_lines(path)
        assert [line["event"] for line in lines] == ["jsonrpc.request.sent", "stderr.line"]
        assert lines[0]["data"]["method"] == "initialize"
        assert lines[1]["data"]["line"] == "oops"

    def test_canonical_log_captures_normalized_events_only(self, tmp_path):
        path = tmp_path / "canonical.ndjson"
        logger = CanonicalLogger(path)

        logger.log_canonical("session.started", {"provider": "codex"})
        logger.log_canonical("turn.completed", {"turn": {"id": "turn-1"}})

        lines = _read_lines(path)
        assert [line["event"] for line in lines] == ["session.started", "turn.completed"]
        assert lines[0]["data"] == {"provider": "codex"}
        assert lines[1]["data"]["turn"]["id"] == "turn-1"
