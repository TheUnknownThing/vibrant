"""Provider selection and metadata helpers."""

from __future__ import annotations

from typing import Protocol

from .base import ProviderAdapter, ProviderKind


class ProviderAdapterFactory(Protocol):
    def __call__(self, *args: object, **kwargs: object) -> ProviderAdapter: ...


class ProviderConfig(Protocol):
    mock_responses: bool
    provider_kind: ProviderKind | str | None

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


def resolve_provider_adapter(value: ProviderKind | str | None) -> type[ProviderAdapter]:
    """Return the adapter class for a provider kind."""

    kind = normalize_provider_kind(value)
    if kind is ProviderKind.CODEX:
        from .codex.adapter import CodexProviderAdapter

        return CodexProviderAdapter
    if kind is ProviderKind.CLAUDE:
        from .claude.adapter import ClaudeProviderAdapter

        return ClaudeProviderAdapter
    raise ValueError(f"Unsupported provider kind: {value!r}")


def resolve_configured_adapter_factory(
    config: ProviderConfig,
    adapter_factory: ProviderAdapterFactory | None = None,
) -> ProviderAdapterFactory:
    """Resolve the adapter factory for a runtime configuration."""

    if adapter_factory is not None:
        return adapter_factory
    if bool(getattr(config, "mock_responses", False)):
        from .mock.adapter import MockCodexAdapter

        return MockCodexAdapter
    return resolve_provider_adapter(getattr(config, "provider_kind", None))
