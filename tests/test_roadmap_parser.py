"""Unit tests for the Phase 3 roadmap parser and prompt builder."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from vibrant.consensus.roadmap import RoadmapParser
from vibrant.models.consensus import ConsensusDocument, ConsensusStatus
from vibrant.models.task import TaskInfo, TaskStatus


SAMPLE_ROADMAP = """# Roadmap — Project Vibrant

### Task task-001 — Build configuration loader
- **Status**: pending
- **Priority**: high
- **Dependencies**: none
- **Skills**: testing-strategy, config
- **Branch**: vibrant/task-001
- **Prompt**: Implement `.vibrant/vibrant.toml` loading.

**Acceptance Criteria**:
- [ ] Load config from the project root
- [ ] Apply defaults when the file is missing

### Task task-002 — Start orchestrator using config
- **Status**: pending
- **Priority**: medium
- **Dependencies**: task-001
- **Skills**: orchestration
- **Branch**: vibrant/task-002
- **Prompt**: Use the config loader during startup.

**Acceptance Criteria**:
- [ ] Read persisted state on startup
- [ ] Honor configured concurrency
"""


CYCLIC_ROADMAP = """# Roadmap — Project Vibrant

### Task task-a — First task
- **Status**: pending
- **Dependencies**: task-b
- **Skills**: none
- **Priority**: low

**Acceptance Criteria**:
- [ ] Do task A

### Task task-b — Second task
- **Status**: pending
- **Dependencies**: task-a
- **Skills**: none
- **Priority**: low

**Acceptance Criteria**:
- [ ] Do task B
"""


class TestRoadmapParser:
    def test_parse_sample_roadmap_into_ordered_tasks(self):
        document = RoadmapParser().parse(SAMPLE_ROADMAP)

        assert document.project == "Vibrant"
        assert [task.id for task in document.tasks] == ["task-001", "task-002"]
        assert document.tasks[0].title == "Build configuration loader"
        assert document.tasks[0].priority == 1
        assert document.tasks[0].dependencies == []
        assert document.tasks[0].skills == ["testing-strategy", "config"]
        assert document.tasks[0].acceptance_criteria == [
            "Load config from the project root",
            "Apply defaults when the file is missing",
        ]
        assert document.tasks[1].dependencies == ["task-001"]
        assert document.tasks[1].prompt == "Use the config loader during startup."

    def test_dependency_graph_validation_rejects_cycles(self):
        parser = RoadmapParser()

        with pytest.raises(ValueError, match="dependency graph contains a cycle"):
            parser.parse(CYCLIC_ROADMAP)

    def test_prompt_generation_includes_spec_fields(self):
        parser = RoadmapParser()
        task = parser.parse(SAMPLE_ROADMAP).tasks[0]
        consensus = ConsensusDocument(
            project="Vibrant",
            created_at=datetime(2026, 3, 7, 22, 0, tzinfo=timezone.utc),
            updated_at=datetime(2026, 3, 8, 1, 0, tzinfo=timezone.utc),
            version=4,
            status=ConsensusStatus.PLANNING,
            context="## Objectives\nShip roadmap parsing and prompt generation.\n\n## Getting Started\nRead docs/spec.md and inspect `.vibrant/roadmap.md`.",
        )

        prompt = parser.build_task_prompt(
            task,
            consensus,
            additional_context="Relevant files:\n- vibrant/consensus/roadmap.py",
            skill_contents=["# testing-strategy\nWrite focused tests before broader validation."],
        )

        assert "You are a code agent working on Project Vibrant." in prompt
        assert "## Your Task" in prompt
        assert "Build configuration loader" in prompt
        assert "## Acceptance Criteria" in prompt
        assert "- [ ] Load config from the project root" in prompt
        assert "## Context" in prompt
        assert "Consensus Status: PLANNING" in prompt
        assert "Consensus Version: 4" in prompt
        assert "Consensus Context:" in prompt
        assert "Ship roadmap parsing and prompt generation." in prompt
        assert "Relevant files:" in prompt
        assert "## Skills" in prompt
        assert "# testing-strategy" in prompt
        assert "## Rules" in prompt
        assert "`vibrant/task-001`" in prompt
        assert "Do NOT modify orchestrator-owned `.vibrant` state" in prompt
        assert "describe the proposed change in your summary" in prompt
        assert "[vibrant:task-001]" in prompt

    def test_update_task_status_rewrites_roadmap(self, tmp_path):
        roadmap_path = tmp_path / ".vibrant" / "roadmap.md"
        roadmap_path.parent.mkdir(parents=True)
        roadmap_path.write_text(SAMPLE_ROADMAP, encoding="utf-8")

        parser = RoadmapParser()
        updated = parser.update_task_status(roadmap_path, "task-001", TaskStatus.QUEUED)
        reparsed = parser.parse_file(roadmap_path)

        assert updated.tasks[0].status is TaskStatus.QUEUED
        assert reparsed.tasks[0].status is TaskStatus.QUEUED
        assert "- **Status**: queued" in roadmap_path.read_text(encoding="utf-8")

    def test_update_task_status_rejects_invalid_lifecycle_jump(self, tmp_path):
        roadmap_path = tmp_path / ".vibrant" / "roadmap.md"
        roadmap_path.parent.mkdir(parents=True)
        roadmap_path.write_text(SAMPLE_ROADMAP, encoding="utf-8")

        with pytest.raises(ValueError, match="Invalid task status transition"):
            RoadmapParser().update_task_status(roadmap_path, "task-001", TaskStatus.ACCEPTED)

    def test_multiline_prompt_round_trips_through_write_and_status_update(self, tmp_path):
        roadmap_path = tmp_path / ".vibrant" / "roadmap.md"
        roadmap_path.parent.mkdir(parents=True)
        parser = RoadmapParser()
        parser.write(
            roadmap_path,
            parser.parse(
                """# Roadmap — Project Vibrant

### Task task-001 — Build configuration loader
- **Status**: pending
- **Priority**: high
- **Dependencies**: none
- **Skills**: config
- **Branch**: vibrant/task-001
- **Prompt**: First line
Second line

**Acceptance Criteria**:
- [ ] Keep both lines
"""
            )
        )

        reparsed = parser.parse_file(roadmap_path)
        assert reparsed.tasks[0].prompt == "First line\nSecond line"

        updated = parser.update_task_status(roadmap_path, "task-001", TaskStatus.QUEUED)
        assert updated.tasks[0].prompt == "First line\nSecond line"
        assert parser.parse_file(roadmap_path).tasks[0].prompt == "First line\nSecond line"
        assert "**Prompt**:\nFirst line\nSecond line" in roadmap_path.read_text(encoding="utf-8")
