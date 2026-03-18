from __future__ import annotations

from pathlib import Path

from vibrant.orchestrator import create_orchestrator
from vibrant.orchestrator import types as orchestrator_types
from vibrant.orchestrator.policy.gatekeeper_loop import GatekeeperLifecycleService
from vibrant.orchestrator.policy.task_loop import ExecutionCoordinator
from vibrant.project_init import initialize_project


def test_bootstrap_uses_policy_owned_workflow_runners(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    orchestrator = create_orchestrator(tmp_path)

    assert isinstance(orchestrator._gatekeeper_lifecycle, GatekeeperLifecycleService)
    assert orchestrator._gatekeeper_lifecycle.__class__.__module__.startswith(
        "vibrant.orchestrator.policy.gatekeeper_loop"
    )
    assert isinstance(orchestrator._execution_coordinator, ExecutionCoordinator)
    assert orchestrator._execution_coordinator.__class__.__module__.startswith(
        "vibrant.orchestrator.policy.task_loop"
    )
    assert not hasattr(orchestrator, "gatekeeper_lifecycle")
    assert not hasattr(orchestrator, "execution_coordinator")
    assert not hasattr(orchestrator, "workflow_state_store")
    assert not hasattr(orchestrator, "attempt_store")
    assert not hasattr(orchestrator, "control_plane")
    assert not hasattr(orchestrator, "backend")
    assert not hasattr(orchestrator, "config")
    assert not hasattr(orchestrator, "binding_service")


def test_shared_types_exclude_policy_owned_models() -> None:
    for name in (
        "DispatchLease",
        "GatekeeperMessageKind",
        "GatekeeperSubmission",
        "PreparedTaskExecution",
        "ReviewResolutionCommand",
        "TaskState",
    ):
        assert not hasattr(orchestrator_types, name)
