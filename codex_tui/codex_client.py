"""Async JSON-RPC client for the Codex app-server.

Spawns `codex app-server` as a subprocess, communicates via
newline-delimited JSON over stdio.  Handles request/response
correlation, notification dispatch, and graceful shutdown.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from typing import Any, Callable, Coroutine

from .models import JsonRpcNotification, JsonRpcRequest, JsonRpcResponse

logger = logging.getLogger(__name__)

# How long to wait for a JSON-RPC response before timing out
DEFAULT_REQUEST_TIMEOUT_S = 120
# How long to wait for the process to terminate on shutdown
SHUTDOWN_GRACE_S = 5


class CodexClientError(Exception):
    """Raised when the Codex app-server returns an error or is unreachable."""


class CodexClient:
    """Async JSON-RPC client wrapping a single ``codex app-server`` process.

    Usage::

        client = CodexClient(cwd="/my/project", codex_binary="codex")
        await client.start()
        result = await client.send_request("thread/start", {"model": "gpt-5.3-codex"})
        await client.stop()
    """

    def __init__(
        self,
        *,
        cwd: str | None = None,
        codex_binary: str = "codex",
        codex_home: str | None = None,
        on_notification: Callable[[JsonRpcNotification], Coroutine[Any, Any, None]] | None = None,
        on_stderr: Callable[[str], None] | None = None,
    ) -> None:
        self._cwd = cwd or os.getcwd()
        self._codex_binary = codex_binary
        self._codex_home = codex_home
        self._on_notification = on_notification
        self._on_stderr = on_stderr

        self._process: asyncio.subprocess.Process | None = None
        self._read_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None

        self._next_id = 1
        self._pending: dict[int | str, asyncio.Future[Any]] = {}
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Spawn ``codex app-server`` and begin reading its stdout."""
        if self._running:
            raise CodexClientError("Client is already running")

        env = {**os.environ}
        if self._codex_home:
            env["CODEX_HOME"] = self._codex_home

        # Use a large buffer limit (16 MB) — codex can send very long
        # JSON lines for items with large aggregatedOutput.
        self._process = await asyncio.create_subprocess_exec(
            self._codex_binary, "app-server",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._cwd,
            env=env,
            limit=16 * 1024 * 1024,  # 16 MB stream buffer
        )
        self._running = True
        self._read_task = asyncio.create_task(self._read_loop(), name="codex-read")
        self._stderr_task = asyncio.create_task(self._stderr_loop(), name="codex-stderr")
        logger.info("codex app-server started (pid=%s, cwd=%s)", self._process.pid, self._cwd)

    async def stop(self) -> None:
        """Gracefully shut down the subprocess."""
        if not self._running:
            return
        self._running = False

        # Cancel all pending requests
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(CodexClientError("Client shutting down"))
        self._pending.clear()

        proc = self._process
        if proc and proc.returncode is None:
            try:
                proc.send_signal(signal.SIGTERM)
                try:
                    await asyncio.wait_for(proc.wait(), timeout=SHUTDOWN_GRACE_S)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
            except ProcessLookupError:
                pass

        for task in (self._read_task, self._stderr_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        logger.info("codex app-server stopped")

    @property
    def is_running(self) -> bool:
        return self._running and self._process is not None and self._process.returncode is None

    # ------------------------------------------------------------------
    # JSON-RPC messaging
    # ------------------------------------------------------------------

    async def send_request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = DEFAULT_REQUEST_TIMEOUT_S,
    ) -> Any:
        """Send a JSON-RPC request and wait for the response.

        Returns the ``result`` field on success, or raises
        :class:`CodexClientError` on error / timeout.
        """
        if not self.is_running:
            raise CodexClientError("Client is not running")

        req_id = self._next_id
        self._next_id += 1

        request = JsonRpcRequest(id=req_id, method=method, params=params)
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = future

        self._write(request.to_line())
        logger.debug("→ %s (id=%s)", method, req_id)

        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise CodexClientError(f"Request {method} (id={req_id}) timed out after {timeout}s")

    def send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Send a fire-and-forget notification (no id, no response)."""
        if not self.is_running:
            raise CodexClientError("Client is not running")
        msg = {"method": method}
        if params is not None:
            msg["params"] = params
        self._write(json.dumps(msg))
        logger.debug("→ notification %s", method)

    # ------------------------------------------------------------------
    # Internal I/O
    # ------------------------------------------------------------------

    def _write(self, line: str) -> None:
        """Write a single JSONL message to stdin."""
        proc = self._process
        if proc and proc.stdin:
            proc.stdin.write((line + "\n").encode())
            # Don't await drain here — buffering is fine for JSONL

    async def _read_loop(self) -> None:
        """Read stdout line-by-line, dispatch responses and notifications."""
        proc = self._process
        if not proc or not proc.stdout:
            return
        try:
            while self._running:
                raw = await proc.stdout.readline()
                if not raw:
                    break  # EOF — process exited
                line = raw.decode().strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("Non-JSON line from codex: %s", line[:200])
                    continue
                await self._dispatch(msg)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Error in codex read loop")
        finally:
            # If the process exited unexpectedly, fail pending requests
            if self._running:
                self._running = False
                for fut in self._pending.values():
                    if not fut.done():
                        fut.set_exception(CodexClientError("codex app-server exited unexpectedly"))
                self._pending.clear()

    async def _stderr_loop(self) -> None:
        """Read stderr for log messages."""
        proc = self._process
        if not proc or not proc.stderr:
            return
        try:
            while self._running:
                raw = await proc.stderr.readline()
                if not raw:
                    break
                line = raw.decode().strip()
                if line and self._on_stderr:
                    self._on_stderr(line)
                elif line:
                    logger.debug("codex stderr: %s", line[:300])
        except asyncio.CancelledError:
            return

    async def _dispatch(self, msg: dict[str, Any]) -> None:
        """Route an incoming message to the correct handler."""
        if "id" in msg:
            # This is a response to one of our requests
            req_id = msg["id"]
            future = self._pending.pop(req_id, None)
            if future and not future.done():
                if "error" in msg and msg["error"]:
                    error_msg = msg["error"].get("message", "Unknown error")
                    error_code = msg["error"].get("code", -1)
                    future.set_exception(
                        CodexClientError(f"[{error_code}] {error_msg}")
                    )
                else:
                    future.set_result(msg.get("result"))
            elif "method" in msg:
                # This is a *request* from the server (e.g. approval request)
                # The server sends requests with both "id" and "method"
                await self._handle_server_request(msg)
            else:
                logger.warning("Response for unknown request id=%s", req_id)
        elif "method" in msg:
            # Notification (no id)
            notification = JsonRpcNotification(
                method=msg["method"],
                params=msg.get("params"),
            )
            logger.debug("← notification %s", notification.method)
            if self._on_notification:
                try:
                    await self._on_notification(notification)
                except Exception:
                    logger.exception("Error in notification handler for %s", notification.method)

    async def _handle_server_request(self, msg: dict[str, Any]) -> None:
        """Handle server-initiated requests (approval, user input).

        We wrap these as notifications to the higher layer which will
        decide how to respond.
        """
        notification = JsonRpcNotification(
            method=msg["method"],
            params={
                **(msg.get("params") or {}),
                "_jsonrpc_id": msg["id"],  # stash the id so we can respond
            },
        )
        logger.debug("← server request %s (id=%s)", msg["method"], msg["id"])
        if self._on_notification:
            try:
                await self._on_notification(notification)
            except Exception:
                logger.exception("Error handling server request %s", msg["method"])

    def respond_to_server_request(
        self,
        jsonrpc_id: int | str,
        result: Any = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        """Send a response back to a server-initiated request."""
        response: dict[str, Any] = {"id": jsonrpc_id}
        if error:
            response["error"] = error
        else:
            response["result"] = result if result is not None else {}
        self._write(json.dumps(response))
        logger.debug("→ response to server request id=%s", jsonrpc_id)
