"""Unit tests for the CodexClient JSON-RPC layer."""

from __future__ import annotations

import asyncio
import json
import pytest

from vibrant.providers.codex.client import CodexClient, CodexClientError
from vibrant.models.wire import JsonRpcRequest, JsonRpcNotification


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------

class TestJsonRpcRequest:
    def test_to_line_basic(self):
        req = JsonRpcRequest(id=1, method="test/method", params={"key": "value"})
        line = req.to_line()
        parsed = json.loads(line)
        assert parsed["id"] == 1
        assert parsed["method"] == "test/method"
        assert parsed["params"]["key"] == "value"
        assert "jsonrpc" not in parsed  # Codex uses "JSON-RPC lite"

    def test_to_line_no_params(self):
        req = JsonRpcRequest(id=42, method="initialized")
        line = req.to_line()
        parsed = json.loads(line)
        assert parsed["id"] == 42
        assert parsed["method"] == "initialized"
        assert "params" not in parsed


class TestJsonRpcNotification:
    def test_init(self):
        n = JsonRpcNotification(method="turn/started", params={"turn": {"id": "t1"}})
        assert n.method == "turn/started"
        assert n.params is not None
        assert n.params["turn"]["id"] == "t1"


# ---------------------------------------------------------------------------
# Client instantiation tests (no subprocess)
# ---------------------------------------------------------------------------

class TestCodexClientInit:
    def test_default_state(self):
        client = CodexClient()
        assert not client.is_running

    def test_custom_params(self):
        client = CodexClient(cwd="/tmp", codex_binary="/usr/bin/codex")
        assert client._cwd == "/tmp"
        assert client._codex_binary == "/usr/bin/codex"

    @pytest.mark.asyncio
    async def test_send_request_when_not_running(self):
        client = CodexClient()
        with pytest.raises(CodexClientError, match="not running"):
            await client.send_request("test", {})

    def test_send_notification_when_not_running(self):
        client = CodexClient()
        with pytest.raises(CodexClientError, match="not running"):
            client.send_notification("test", {})


# ---------------------------------------------------------------------------
# Dispatch tests (mock the internal state)
# ---------------------------------------------------------------------------

class TestDispatch:
    @pytest.mark.asyncio
    async def test_response_resolves_future(self):
        client = CodexClient()
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        client._pending[1] = future

        await client._dispatch({"id": 1, "result": {"ok": True}})
        assert future.done()
        assert future.result() == {"ok": True}

    @pytest.mark.asyncio
    async def test_error_response_raises(self):
        client = CodexClient()
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        client._pending[2] = future

        await client._dispatch({"id": 2, "error": {"code": -1, "message": "fail"}})
        assert future.done()
        with pytest.raises(CodexClientError, match="fail"):
            future.result()

    @pytest.mark.asyncio
    async def test_notification_callback(self):
        received = []

        async def handler(n: JsonRpcNotification):
            received.append(n)

        client = CodexClient(on_notification=handler)
        await client._dispatch({"method": "turn/started", "params": {"turn": {"id": "t1"}}})

        assert len(received) == 1
        assert received[0].method == "turn/started"

    @pytest.mark.asyncio
    async def test_unknown_response_id_ignored(self):
        client = CodexClient()
        # Should not raise
        await client._dispatch({"id": 999, "result": None})


# ---------------------------------------------------------------------------
# Server request handling
# ---------------------------------------------------------------------------

class TestServerRequest:
    @pytest.mark.asyncio
    async def test_server_request_forwarded_as_notification(self):
        received = []

        async def handler(n: JsonRpcNotification):
            received.append(n)

        client = CodexClient(on_notification=handler)
        # Simulate a server request (has both id and method)
        await client._handle_server_request({
            "id": 10,
            "method": "item/commandExecution/requestApproval",
            "params": {"command": "rm -rf /"},
        })

        assert len(received) == 1
        assert received[0].method == "item/commandExecution/requestApproval"
        assert received[0].params["_jsonrpc_id"] == 10
        assert received[0].params["command"] == "rm -rf /"

    def test_respond_to_server_request(self):
        client = CodexClient()
        # Create a process mock to capture writes
        written = []

        class FakeStdin:
            def write(self, data):
                written.append(data)

        class FakeProcess:
            stdin = FakeStdin()
            returncode = None

        client._process = FakeProcess()
        client._running = True

        client.respond_to_server_request(10, result={"approved": True})
        assert len(written) == 1
        msg = json.loads(written[0].decode().strip())
        assert msg["id"] == 10
        assert msg["result"]["approved"] is True
