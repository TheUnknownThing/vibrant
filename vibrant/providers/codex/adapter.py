"""Codex-backed implementation of the provider adapter interface."""

from __future__ import annotations

from typing import Any

from ..base import ProviderAdapter
from .client import CodexClient


class CodexProviderAdapter(ProviderAdapter):
    """Minimal adapter over :class:`CodexClient`."""

    def __init__(self, client: CodexClient) -> None:
        self.client = client

    async def start(self) -> None:
        await self.client.start()

    async def stop(self) -> None:
        await self.client.stop()

    async def send_message(self, prompt: str, **kwargs: Any) -> Any:
        payload = {"text": prompt, **kwargs}
        return await self.client.send_request("conversation/send", payload)

