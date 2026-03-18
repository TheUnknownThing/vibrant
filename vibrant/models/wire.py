"""JSON-RPC wire models for the Codex provider transport."""

from __future__ import annotations

from pydantic import BaseModel, JsonValue

from vibrant.type_defs import RequestId


class JsonRpcRequest(BaseModel):
    """Outgoing JSON-RPC request."""

    id: RequestId
    method: str
    params: dict[str, JsonValue] | None = None

    def to_line(self) -> str:
        return self.model_dump_json(exclude_none=True)


class JsonRpcResponse(BaseModel):
    """Incoming JSON-RPC response."""

    id: RequestId
    result: JsonValue | None = None
    error: dict[str, JsonValue] | None = None

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
    params: dict[str, JsonValue] | None = None
