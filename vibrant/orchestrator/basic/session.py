"""Small helpers for runtime-backed durable state."""

from __future__ import annotations

from vibrant.models.agent import AgentProviderMetadata, ProviderResumeHandle


def authoritative_resume_handle(handle: ProviderResumeHandle | None) -> ProviderResumeHandle | None:
    """Drop empty provider resume metadata from projections."""

    if handle is None or handle.empty:
        return None
    return handle


def carry_forward_resume_handle(
    target_provider: AgentProviderMetadata,
    source_provider: AgentProviderMetadata | None,
) -> None:
    """Preserve the last durable resume handle when reusing a logical run id."""

    if authoritative_resume_handle(ProviderResumeHandle.from_provider_metadata(target_provider)) is not None:
        return
    if source_provider is None:
        return
    handle = authoritative_resume_handle(ProviderResumeHandle.from_provider_metadata(source_provider))
    if handle is None:
        return
    target_provider.set_resume_handle(handle.model_copy(deep=True))
