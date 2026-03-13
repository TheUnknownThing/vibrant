"""Agent-related orchestrator components."""

from __future__ import annotations

__all__ = [
    "AgentInstance",
    "ManagedAgentInstance",
    "StartedAgentRun",
    "AgentManagementService",
    "AgentRoleCatalog",
    "AgentRoleSpec",
    "AgentOutputProjectionService",
    "ManagedAgentSnapshot",
    "AgentInstanceStore",
    "AgentRecordStore",
    "AgentRegistry",
    "AgentRuntimeService",
    "ProviderKindCatalog",
    "ProviderKindSpec",
    "RuntimeHandleSnapshot",
    "build_builtin_provider_catalog",
    "build_builtin_role_catalog",
]


def __getattr__(name: str):
    if name in {
        "AgentRoleCatalog",
        "AgentRoleSpec",
        "ProviderKindCatalog",
        "ProviderKindSpec",
        "build_builtin_provider_catalog",
        "build_builtin_role_catalog",
    }:
        from .catalog import (
            AgentRoleCatalog,
            AgentRoleSpec,
            ProviderKindCatalog,
            ProviderKindSpec,
            build_builtin_provider_catalog,
            build_builtin_role_catalog,
        )

        return {
            "AgentRoleCatalog": AgentRoleCatalog,
            "AgentRoleSpec": AgentRoleSpec,
            "ProviderKindCatalog": ProviderKindCatalog,
            "ProviderKindSpec": ProviderKindSpec,
            "build_builtin_provider_catalog": build_builtin_provider_catalog,
            "build_builtin_role_catalog": build_builtin_role_catalog,
        }[name]
    if name in {"AgentInstance", "ManagedAgentInstance", "StartedAgentRun"}:
        from .instance import AgentInstance, ManagedAgentInstance, StartedAgentRun

        return {
            "AgentInstance": AgentInstance,
            "ManagedAgentInstance": ManagedAgentInstance,
            "StartedAgentRun": StartedAgentRun,
        }[name]
    if name in {"AgentManagementService", "ManagedAgentSnapshot"}:
        from .manager import AgentManagementService, ManagedAgentSnapshot

        return {
            "AgentManagementService": AgentManagementService,
            "ManagedAgentSnapshot": ManagedAgentSnapshot,
        }[name]
    if name == "AgentOutputProjectionService":
        from .output_projection import AgentOutputProjectionService

        return AgentOutputProjectionService
    if name == "AgentRegistry":
        from .registry import AgentRegistry

        return AgentRegistry
    if name in {"AgentRuntimeService", "RuntimeHandleSnapshot"}:
        from .runtime import AgentRuntimeService, RuntimeHandleSnapshot

        return {
            "AgentRuntimeService": AgentRuntimeService,
            "RuntimeHandleSnapshot": RuntimeHandleSnapshot,
        }[name]
    if name in {"AgentInstanceStore", "AgentRecordStore"}:
        from .store import AgentInstanceStore, AgentRecordStore

        return {"AgentInstanceStore": AgentInstanceStore, "AgentRecordStore": AgentRecordStore}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
