"""Loopback HTTP transport lifecycle for the orchestrator FastMCP host."""

from __future__ import annotations

import asyncio
import socket
from typing import Any

import uvicorn


class _EmbeddedUvicornServer(uvicorn.Server):
    """Uvicorn server wrapper that avoids installing signal handlers."""

    def install_signal_handlers(self) -> None:  # pragma: no cover - exercised indirectly
        return None


class LoopbackHTTPTransport:
    """Manage a loopback-only uvicorn server for the MCP host."""

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int | None = None,
        path: str = "/mcp",
        log_level: str = "warning",
    ) -> None:
        self.host = host
        self.port = port
        self.path = path
        self.log_level = log_level
        self._server: _EmbeddedUvicornServer | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def endpoint_url(self) -> str | None:
        if self.port is None:
            return None
        return f"http://{self.host}:{self.port}{self.path}"

    async def start(self, app: Any) -> str:
        """Start serving the provided ASGI app on loopback HTTP."""

        if self.running:
            assert self.endpoint_url is not None
            return self.endpoint_url

        resolved_port = self.port or _allocate_loopback_port(self.host)
        config = uvicorn.Config(
            app,
            host=self.host,
            port=resolved_port,
            log_level=self.log_level,
            access_log=False,
            lifespan="on",
        )
        server = _EmbeddedUvicornServer(config)
        task = asyncio.create_task(server.serve(), name=f"vibrant-mcp-http-{resolved_port}")

        self.port = resolved_port
        self._server = server
        self._task = task

        for _ in range(200):
            if server.started:
                assert self.endpoint_url is not None
                return self.endpoint_url
            if task.done():
                await _await_server_task(task)
                raise RuntimeError("Loopback MCP server exited before startup completed")
            await asyncio.sleep(0.01)

        await self.stop()
        raise RuntimeError("Timed out waiting for the loopback MCP server to start")

    async def stop(self) -> None:
        """Stop the loopback HTTP server if it is running."""

        server = self._server
        task = self._task

        self._server = None
        self._task = None

        if server is None or task is None:
            return

        server.should_exit = True
        try:
            await task
        finally:
            self.port = None


async def _await_server_task(task: asyncio.Task[None]) -> None:
    try:
        await task
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        raise RuntimeError("Loopback MCP server crashed during startup") from exc


def _allocate_loopback_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return int(sock.getsockname()[1])
