"""Tests for the Panel C consensus widget and app wiring."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from textual.app import App, ComposeResult

from vibrant.consensus import ConsensusParser, RoadmapParser
from vibrant.models.state import GatekeeperStatus, OrchestratorState, OrchestratorStatus
from vibrant.models.task import TaskInfo, TaskStatus
from vibrant.project_init import initialize_project
from vibrant.tui.app import VibrantApp
from vibrant.tui.widgets.consensus_view import ConsensusMarkdownScreen, ConsensusView


SAMPLE_CONSENSUS = """# Consensus Pool — Project Vibrant
<!-- META:START -->
- **Project**: Vibrant
- **Created**: 2026-03-07T22:00:00Z
- **Last Updated**: 2026-03-08T01:15:00Z
- **Version**: 7
- **Status**: EXECUTING
<!-- META:END -->
## Objectives
<!-- OBJECTIVES:START -->
Ship the orchestrator core.
<!-- OBJECTIVES:END -->
## Design Choices
<!-- DECISIONS:START -->
### Decision 1: Use Markdown sections
- **Date**: 2026-03-07T22:30:00Z
- **Made By**: `gatekeeper`
- **Context**: Agents need shared context.
- **Resolution**: Keep consensus structured.
- **Impact**: Parser and writer depend on delimiters.

### Decision 2: Pause support in state machine
- **Date**: 2026-03-08T00:00:00Z
- **Made By**: `user`
- **Context**: Operator needs control.
- **Resolution**: Add PAUSED state.
- **Impact**: TUI and engine both surface pause.

### Decision 3: Use isolated worktrees
- **Date**: 2026-03-08T00:30:00Z
- **Made By**: `gatekeeper`
- **Context**: Agents need isolated branches.
- **Resolution**: Create one worktree per task.
- **Impact**: Enables concurrent execution.

### Decision 4: Highlight blocking questions
- **Date**: 2026-03-08T01:00:00Z
- **Made By**: `gatekeeper`
- **Context**: Users must notice escalations quickly.
- **Resolution**: Highlight pending questions in Panel C.
- **Impact**: Improves operator awareness.
<!-- DECISIONS:END -->
## Getting Started
Read `docs/spec.md` first, then `.vibrant/roadmap.md`.
## Questions
- **Priority**: blocking | **Question**: Should auth use OAuth or API keys?
"""

SAMPLE_ROADMAP = """# Roadmap — Project Vibrant

### Task task-001 — Ship parser updates
- **Status**: accepted
- **Priority**: high
- **Dependencies**: none
- **Skills**: none
- **Branch**: vibrant/task-001
- **Retry Count**: 0
- **Max Retries**: 3

**Acceptance Criteria**:
- [ ] Parse consensus markdown

### Task task-002 — Build task dispatch
- **Status**: completed
- **Priority**: medium
- **Dependencies**: task-001
- **Skills**: none
- **Branch**: vibrant/task-002
- **Retry Count**: 0
- **Max Retries**: 3

**Acceptance Criteria**:
- [ ] Dispatch ready tasks

### Task task-003 — Surface questions in the TUI
- **Status**: pending
- **Priority**: high
- **Dependencies**: task-002
- **Skills**: none
- **Branch**: vibrant/task-003
- **Retry Count**: 0
- **Max Retries**: 3

**Acceptance Criteria**:
- [ ] Show pending question count

### Task task-004 — Polish overlays
- **Status**: failed
- **Priority**: low
- **Dependencies**: task-003
- **Skills**: none
- **Branch**: vibrant/task-004
- **Retry Count**: 1
- **Max Retries**: 3

**Acceptance Criteria**:
- [ ] Show the full consensus overlay
"""


class ConsensusHarness(App):
    def __init__(self, *, markdown: str, tasks: list[TaskInfo]) -> None:
        super().__init__()
        self._markdown = markdown
        self._tasks = tasks

    def compose(self) -> ComposeResult:
        yield ConsensusView(id="consensus")

    async def on_mount(self) -> None:
        document = ConsensusParser().parse(self._markdown)
        self.query_one(ConsensusView).update_consensus(document, tasks=self._tasks, raw_markdown=self._markdown)


class FakeLifecycle:
    def __init__(self, project_root: str | Path, *, on_canonical_event=None) -> None:
        self.project_root = Path(project_root)
        self.on_canonical_event = on_canonical_event
        self.roadmap_path = self.project_root / ".vibrant" / "roadmap.md"
        self.consensus_path = self.project_root / ".vibrant" / "consensus.md"
        self.engine = SimpleNamespace(
            agents={},
            consensus=None,
            consensus_path=self.consensus_path,
            USER_INPUT_BANNER="⚠ Gatekeeper needs your input — see Chat panel",
            state=OrchestratorState(
                session_id="session-1",
                status=OrchestratorStatus.EXECUTING,
                gatekeeper_status=GatekeeperStatus.AWAITING_USER,
                pending_questions=["Should auth use OAuth or API keys?"],
            ),
        )
        self._roadmap_parser = RoadmapParser()
        self._consensus_parser = ConsensusParser()

    def reload_from_disk(self):
        self.engine.consensus = self._consensus_parser.parse_file(self.consensus_path)
        return self._roadmap_parser.parse_file(self.roadmap_path)


async def _shutdown_default_executor() -> None:
    loop = asyncio.get_running_loop()
    executor = getattr(loop, "_default_executor", None)
    if executor is None:
        return
    executor.shutdown(wait=True, cancel_futures=True)
    loop._default_executor = None


@asynccontextmanager
async def _run_test(app):
    async with app.run_test() as pilot:
        yield pilot
    await _shutdown_default_executor()


def test_consensus_view_summary_shows_counts_and_recent_decisions():
    tasks = [
        TaskInfo(id="task-001", title="Accepted task", status=TaskStatus.ACCEPTED),
        TaskInfo(id="task-002", title="Completed task", status=TaskStatus.COMPLETED),
        TaskInfo(id="task-003", title="Pending task", status=TaskStatus.PENDING),
        TaskInfo(id="task-004", title="Failed task", status=TaskStatus.FAILED, retry_count=1),
    ]
    widget = ConsensusView()
    document = ConsensusParser().parse(SAMPLE_CONSENSUS)
    widget.update_consensus(document, tasks=tasks, raw_markdown=SAMPLE_CONSENSUS)

    assert "Status: EXECUTING" in widget.get_summary_text()
    assert "Version: 7" in widget.get_summary_text()
    assert "Tasks: 2/4" in widget.get_summary_text()

    recent_decisions = widget.get_recent_decisions_text()
    assert "Highlight blocking questions" in recent_decisions
    assert "Use isolated worktrees" in recent_decisions
    assert "Pause support in state machine" in recent_decisions
    assert "Use Markdown sections" not in recent_decisions


def test_consensus_view_highlights_pending_questions_when_present():
    tasks = [TaskInfo(id="task-001", title="Accepted task", status=TaskStatus.ACCEPTED)]
    widget = ConsensusView()
    document = ConsensusParser().parse(SAMPLE_CONSENSUS)
    widget.update_consensus(document, tasks=tasks, raw_markdown=SAMPLE_CONSENSUS)

    assert "Pending Questions: 1" in widget.get_summary_text()
    assert widget.pending_questions_highlighted is True


@pytest.mark.asyncio
async def test_app_f3_opens_full_consensus_markdown_overlay(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    initialize_project(repo)
    (repo / ".vibrant" / "consensus.md").write_text(SAMPLE_CONSENSUS, encoding="utf-8")
    RoadmapParser().write(repo / ".vibrant" / "roadmap.md", RoadmapParser().parse(SAMPLE_ROADMAP))

    app = VibrantApp(cwd=str(repo), lifecycle_factory=FakeLifecycle)
    async with _run_test(app) as pilot:
        await pilot.pause()

        panel = app.query_one(ConsensusView)
        assert "Tasks: 2/4" in panel.get_summary_text()

        await pilot.press("f3")

        assert isinstance(app.screen, ConsensusMarkdownScreen)
        assert "# Consensus Pool — Project Vibrant" in app.screen.markdown_text
        assert "## Questions" in app.screen.markdown_text
