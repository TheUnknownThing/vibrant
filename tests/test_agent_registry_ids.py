from __future__ import annotations

from vibrant.orchestrator import OrchestratorStateBackend
from vibrant.orchestrator.agents.registry import AgentRegistry
from vibrant.orchestrator.agents.store import AgentRecordStore
from vibrant.orchestrator.state import StateStore
from vibrant.project_init import initialize_project


def _make_registry(tmp_path):
    project_root = tmp_path / "repo"
    project_root.mkdir()
    initialize_project(project_root)

    engine = OrchestratorStateBackend.load(project_root)
    state_store = StateStore(engine)
    agent_store = AgentRecordStore(vibrant_dir=project_root / ".vibrant", state_store=state_store)
    return AgentRegistry(agent_store=agent_store, vibrant_dir=project_root / ".vibrant")


def test_resolve_instance_keeps_distinct_task_ids_separate(tmp_path):
    registry = _make_registry(tmp_path)

    underscore = registry.resolve_instance(role="code", scope_type="task", scope_id="task_a")
    hyphen = registry.resolve_instance(role="code", scope_type="task", scope_id="task-a")

    assert underscore.scope.scope_id == "task_a"
    assert hyphen.scope.scope_id == "task-a"
    assert underscore.identity.agent_id != hyphen.identity.agent_id


def test_resolve_instance_is_stable_for_same_scope_id(tmp_path):
    registry = _make_registry(tmp_path)

    first = registry.resolve_instance(role="code", scope_type="task", scope_id="task_a")
    second = registry.resolve_instance(role="code", scope_type="task", scope_id="task_a")

    assert first.identity.agent_id == second.identity.agent_id
