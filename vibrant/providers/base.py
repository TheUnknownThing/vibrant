"""Abstract provider adapter interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ProviderAdapter(ABC):
    """Uniform interface for provider-backed agent runtimes."""

    @abstractmethod
    async def start(self) -> None:
        """Start the provider runtime."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop the provider runtime."""

    @abstractmethod
    async def send_message(self, prompt: str, **kwargs: Any) -> Any:
        """Send a user prompt to the provider."""

