"""Built-in data-driven agent role and provider catalogs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from vibrant.models.agent import AgentRecord

if TYPE_CHECKING:
    from vibrant.agents.runtime import AgentRuntime
    from vibrant.config import VibrantConfig
    from vibrant.providers.base import CanonicalEvent
else:
    AgentRuntime = Any
    VibrantConfig = Any
    CanonicalEvent = dict[str, Any]


def normalize_role_name(value: str) -> str:
    """Normalize a persisted role key."""

    normalized = value.strip().lower()
    if not normalized:
        raise ValueError("Agent role must not be empty")
    return normalized


@dataclass(frozen=True, slots=True)
class ProviderKindSpec:
    """Provider transport metadata resolved from ``provider.kind``."""

    kind: str
    display_name: str
    adapter_factory: Any
    default_transport: str


class ProviderKindCatalog:
    """Lookup container for built-in provider kinds."""

    def __init__(self, specs: list[ProviderKindSpec]) -> None:
        self._specs = {spec.kind: spec for spec in specs}

    def get(self, kind: str) -> ProviderKindSpec:
        normalized = normalize_role_name(kind)
        try:
            return self._specs[normalized]
        except KeyError as exc:
            raise ValueError(f"Unsupported provider kind: {kind!r}") from exc

    def adapter_factory_for(self, kind: str) -> Any:
        return self.get(kind).adapter_factory

    def all(self) -> list[ProviderKindSpec]:
        return list(self._specs.values())


@dataclass(frozen=True, slots=True)
class RoleRuntimeContext:
    """Inputs used to build a runtime for one persisted agent run."""

    project_root: Path
    agent_record: AgentRecord
    config: VibrantConfig
    gatekeeper: Any
    provider_catalog: ProviderKindCatalog
    on_canonical_event: Callable[[CanonicalEvent], Any] | None = None
    on_agent_record_updated: Callable[[AgentRecord], Any] | None = None


RoleRuntimeBuilder = Callable[[RoleRuntimeContext], AgentRuntime]


@dataclass(frozen=True, slots=True)
class AgentRoleSpec:
    """Built-in role metadata used by generic orchestrator services."""

    role: str
    display_name: str
    agent_id_prefix: str
    workflow_class: str
    default_provider_kind: str
    default_runtime_mode: str
    supports_interactive_requests: bool
    persistent_thread: bool
    question_source_role: str | None = None
    contributes_control_plane_status: bool = False
    ui_model_name: str | None = None
    runtime_builder: RoleRuntimeBuilder | None = None

    def build_runtime(self, context: RoleRuntimeContext) -> AgentRuntime:
        if self.runtime_builder is None:
            raise RuntimeError(f"Role {self.role!r} does not expose a runtime builder")
        return self.runtime_builder(context)


class AgentRoleCatalog:
    """Lookup container for built-in agent roles."""

    def __init__(self, specs: list[AgentRoleSpec]) -> None:
        self._specs = {spec.role: spec for spec in specs}

    def get(self, role: str) -> AgentRoleSpec:
        normalized = normalize_role_name(role)
        try:
            return self._specs[normalized]
        except KeyError as exc:
            raise ValueError(f"Unsupported agent role: {role!r}") from exc

    def try_get(self, role: str | None) -> AgentRoleSpec | None:
        if role is None:
            return None
        try:
            return self.get(role)
        except ValueError:
            return None

    def all(self) -> list[AgentRoleSpec]:
        return list(self._specs.values())

    def display_name_for(self, role: str) -> str:
        return self.get(role).display_name

    def ui_model_name_for(self, role: str) -> str:
        spec = self.get(role)
        return spec.ui_model_name or spec.role

    def default_question_source_role(self) -> str:
        for spec in self._specs.values():
            if spec.contributes_control_plane_status and spec.question_source_role:
                return spec.question_source_role
        for spec in self._specs.values():
            if spec.question_source_role:
                return spec.question_source_role
        raise RuntimeError("No built-in role declares a question source role")



def build_builtin_provider_catalog(*, codex_adapter_factory: Any | None = None) -> ProviderKindCatalog:
    """Return the built-in provider catalog."""

    if codex_adapter_factory is None:
        from vibrant.providers.codex.adapter import CodexProviderAdapter

        codex_adapter_factory = CodexProviderAdapter

    return ProviderKindCatalog(
        [
            ProviderKindSpec(
                kind="codex",
                display_name="Codex",
                adapter_factory=codex_adapter_factory,
                default_transport="app-server-json-rpc",
            )
        ]
    )



def build_builtin_role_catalog() -> AgentRoleCatalog:
    """Return the built-in role catalog."""

    return AgentRoleCatalog(
        [
            AgentRoleSpec(
                role="code",
                display_name="Code",
                agent_id_prefix="agent",
                workflow_class="execution",
                default_provider_kind="codex",
                default_runtime_mode="workspace-write",
                supports_interactive_requests=False,
                persistent_thread=False,
                runtime_builder=_build_code_runtime,
            ),
            AgentRoleSpec(
                role="merge",
                display_name="Merge",
                agent_id_prefix="merge",
                workflow_class="merge",
                default_provider_kind="codex",
                default_runtime_mode="danger-full-access",
                supports_interactive_requests=False,
                persistent_thread=False,
                runtime_builder=_build_merge_runtime,
            ),
            AgentRoleSpec(
                role="test",
                display_name="Test",
                agent_id_prefix="test",
                workflow_class="validation",
                default_provider_kind="codex",
                default_runtime_mode="read-only",
                supports_interactive_requests=False,
                persistent_thread=False,
                runtime_builder=_build_code_runtime,
            ),
            AgentRoleSpec(
                role="gatekeeper",
                display_name="Gatekeeper",
                agent_id_prefix="gatekeeper",
                workflow_class="planning-control",
                default_provider_kind="codex",
                default_runtime_mode="read-only",
                supports_interactive_requests=True,
                persistent_thread=True,
                question_source_role="gatekeeper",
                contributes_control_plane_status=True,
                ui_model_name="gatekeeper",
                runtime_builder=_build_gatekeeper_runtime,
            ),
        ]
    )



def _build_code_runtime(context: RoleRuntimeContext) -> AgentRuntime:
    from vibrant.agents.code_agent import CodeAgent
    from vibrant.agents.runtime import BaseAgentRuntime
    from vibrant.agents.utils import parse_runtime_mode

    class _RoleAwareCodeAgent(CodeAgent):
        def get_thread_runtime_mode(self):
            return parse_runtime_mode(context.agent_record.provider.runtime_mode)

        def get_turn_runtime_mode(self):
            return parse_runtime_mode(context.agent_record.provider.runtime_mode)

    agent = _RoleAwareCodeAgent(
        context.project_root,
        context.config,
        adapter_factory=context.provider_catalog.adapter_factory_for(context.agent_record.provider.kind),
        on_canonical_event=context.on_canonical_event,
        on_agent_record_updated=context.on_agent_record_updated,
    )
    return BaseAgentRuntime(agent)



def _build_merge_runtime(context: RoleRuntimeContext) -> AgentRuntime:
    from vibrant.agents.merge_agent import MergeAgent
    from vibrant.agents.runtime import BaseAgentRuntime

    agent = MergeAgent(
        context.project_root,
        context.config,
        adapter_factory=context.provider_catalog.adapter_factory_for(context.agent_record.provider.kind),
        on_canonical_event=context.on_canonical_event,
        on_agent_record_updated=context.on_agent_record_updated,
    )
    return BaseAgentRuntime(agent)



def _build_gatekeeper_runtime(context: RoleRuntimeContext) -> AgentRuntime:
    from vibrant.agents.code_agent import CodeAgent
    from vibrant.agents.gatekeeper import Gatekeeper, GatekeeperAgent
    from vibrant.agents.runtime import BaseAgentRuntime

    context.provider_catalog.get(context.agent_record.provider.kind)

    gatekeeper = context.gatekeeper
    if isinstance(gatekeeper, Gatekeeper):
        gatekeeper_agent = gatekeeper.agent
        agent = GatekeeperAgent(
            context.project_root,
            gatekeeper_agent.config,
            adapter_factory=gatekeeper_agent.adapter_factory,
            on_canonical_event=context.on_canonical_event,
            on_agent_record_updated=context.on_agent_record_updated,
            timeout_seconds=gatekeeper_agent.timeout_seconds,
        )
        return BaseAgentRuntime(agent)

    agent = CodeAgent(
        context.project_root,
        context.config,
        adapter_factory=context.provider_catalog.adapter_factory_for(context.agent_record.provider.kind),
        on_canonical_event=context.on_canonical_event,
        on_agent_record_updated=context.on_agent_record_updated,
    )
    return BaseAgentRuntime(agent)
