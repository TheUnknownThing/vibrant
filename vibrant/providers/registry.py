"""Provider selection and metadata helpers."""

from __future__ import annotations

from typing import Any

from .base import ProviderKind

PROVIDER_TRANSPORTS: dict[ProviderKind, str] = {
    ProviderKind.CODEX: "app-server-json-rpc",
    ProviderKind.CLAUDE: "sdk-stream-json",
}


def normalize_provider_kind(value: ProviderKind | str | None) -> ProviderKind:
    """Return a validated provider kind."""

    if value is None:
        return ProviderKind.CODEX
    if isinstance(value, ProviderKind):
        return value
    return ProviderKind(str(value).strip().lower())


def provider_transport(value: ProviderKind | str | None) -> str:
    """Return the persisted transport token for a provider kind."""

    return PROVIDER_TRANSPORTS[normalize_provider_kind(value)]


def resolve_provider_adapter(value: ProviderKind | str | None) -> Any:
    """Return the adapter class for a provider kind."""

    kind = normalize_provider_kind(value)
    if kind is ProviderKind.CODEX:
        from .codex.adapter import CodexProviderAdapter

        return CodexProviderAdapter
    if kind is ProviderKind.CLAUDE:
        from .claude.adapter import ClaudeProviderAdapter

        return ClaudeProviderAdapter
    raise ValueError(f"Unsupported provider kind: {value!r}")
