from __future__ import annotations

from vibrant.orchestrator.bootstrap import Orchestrator
from vibrant.providers.mock.adapter import MockCodexAdapter


def test_orchestrator_uses_mock_gatekeeper_adapter_when_enabled() -> None:
    orchestrator = Orchestrator.load(".")

    assert orchestrator.gatekeeper.agent.adapter_factory is MockCodexAdapter
