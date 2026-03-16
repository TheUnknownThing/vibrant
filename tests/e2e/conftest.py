from __future__ import annotations

import pytest
import pytest_asyncio

from tests.e2e.artifacts import E2EProjectContext, create_e2e_project_context
from tests.e2e.fixture_provider import FixtureProviderAdapter
from vibrant.agents.gatekeeper import Gatekeeper
from vibrant.orchestrator import create_orchestrator


@pytest.fixture
def e2e_project(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> E2EProjectContext:
    return create_e2e_project_context(
        request.node.name,
        tmp_path_factory=tmp_path_factory,
        artifact_key=request.node.nodeid,
    )


@pytest_asyncio.fixture
async def e2e_orchestrator(e2e_project: E2EProjectContext):
    gatekeeper = Gatekeeper(
        e2e_project.project_root,
        adapter_factory=FixtureProviderAdapter,
    )
    orchestrator = create_orchestrator(
        e2e_project.project_root,
        gatekeeper=gatekeeper,
        adapter_factory=FixtureProviderAdapter,
    )
    e2e_project.snapshot_orchestrator(orchestrator)
    try:
        yield orchestrator
    finally:
        e2e_project.snapshot_orchestrator(orchestrator)
        await orchestrator.shutdown()
