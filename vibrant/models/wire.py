"""JSON-RPC wire models for the Codex provider transport."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class JsonRpcRequest(BaseModel):
    """Outgoing JSON-RPC request."""

    id: int | str
    method: str
    params: dict[str, Any] | None = None

    def to_line(self) -> str:
        return self.model_dump_json(exclude_none=True)


class JsonRpcResponse(BaseModel):
    """Incoming JSON-RPC response."""

    id: int | str
    result: Any | None = None
    error: dict[str, Any] | None = None

    @property
    def is_error(self) -> bool:
        return self.error is not None

    @property
    def error_message(self) -> str:
        if self.error:
            return self.error.get("message", "Unknown error")
        return ""


class JsonRpcNotification(BaseModel):
    """Server-initiated notification."""

    method: str
    params: dict[str, Any] | None = None

