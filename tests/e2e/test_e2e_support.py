"""Focused coverage for backend E2E project and orchestrator fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.e2e.artifacts import VIBRANT_E2E_ARTIFACT_ROOT_ENV, create_e2e_project_context, sanitize_test_name
from tests.e2e.fixture_provider import FixtureProviderAdapter


def test_create_e2e_project_context_uses_stable_artifact_root_when_env_is_set(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    monkeypatch.setenv(VIBRANT_E2E_ARTIFACT_ROOT_ENV, str(tmp_path))

    context = create_e2e_project_context("test stable/artifact root", tmp_path_factory=tmp_path_factory)

    assert context.artifact_root == tmp_path / sanitize_test_name("test stable/artifact root")
    assert context.artifact_key == "test stable/artifact root"
    assert context.project_root == context.artifact_root / "project"
    assert context.worktree_root == context.artifact_root / "worktrees"
    assert context.manifest_path.exists()


def test_create_e2e_project_context_uses_unique_artifact_key_for_stable_roots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    monkeypatch.setenv(VIBRANT_E2E_ARTIFACT_ROOT_ENV, str(tmp_path))

    first = create_e2e_project_context(
        "shared test name",
        tmp_path_factory=tmp_path_factory,
        artifact_key="tests/e2e/test_alpha.py::test_shared[param-a]",
    )
    marker_path = first.artifact_root / "marker.txt"
    marker_path.write_text("keep me\n", encoding="utf-8")

    second = create_e2e_project_context(
        "shared test name",
        tmp_path_factory=tmp_path_factory,
        artifact_key="tests/e2e/test_beta.py::test_shared[param-a]",
    )

    assert first.artifact_root == tmp_path / sanitize_test_name("tests/e2e/test_alpha.py::test_shared[param-a]")
    assert second.artifact_root == tmp_path / sanitize_test_name("tests/e2e/test_beta.py::test_shared[param-a]")
    assert first.artifact_root != second.artifact_root
    assert marker_path.exists()


def test_e2e_project_fixture_initializes_git_repo_and_config(e2e_project) -> None:
    manifest = json.loads(e2e_project.manifest_path.read_text(encoding="utf-8"))
    config_text = (e2e_project.vibrant_dir / "vibrant.toml").read_text(encoding="utf-8")

    assert e2e_project.demo_path.read_text(encoding="utf-8") == "baseline\n"
    assert e2e_project.git("status", "--short") == ""
    assert e2e_project.git("ls-files", "demo.txt") == "demo.txt"
    assert "concurrency-limit = 1" in config_text
    assert str(e2e_project.worktree_root) in config_text
    assert manifest["artifact_key"] == e2e_project.artifact_key
    assert manifest["run_ids"] == []
    assert manifest["question_ids"] == []
    assert manifest["expected_manual_checks"]


@pytest.mark.asyncio
async def test_e2e_orchestrator_fixture_injects_fixture_provider(e2e_project, e2e_orchestrator) -> None:
    assert e2e_orchestrator._adapter_factory is FixtureProviderAdapter
    assert e2e_orchestrator._execution_coordinator.adapter_factory is FixtureProviderAdapter
    assert e2e_orchestrator._gatekeeper.agent.adapter_factory is FixtureProviderAdapter
    assert e2e_orchestrator._config.concurrency_limit == 1

    snapshot = e2e_project.snapshot_orchestrator(e2e_orchestrator)

    assert snapshot["project_root"] == str(e2e_project.project_root)
    assert snapshot["workspace_ids"] == []
