"""Roadmap parsing, validation, persistence, and prompt helpers."""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from vibrant.models.consensus import ConsensusDocument
from vibrant.models.task import TaskInfo, TaskStatus
from vibrant.prompts import build_task_execution_prompt


PRIORITY_NAME_TO_VALUE = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}
VALUE_TO_PRIORITY_NAME = {value: name for name, value in PRIORITY_NAME_TO_VALUE.items()}


@dataclass(slots=True)
class RoadmapDocument:
    """Parsed representation of ``roadmap.md``."""

    project: str = "Vibrant"
    tasks: list[TaskInfo] = field(default_factory=list)


class RoadmapParser:
    """Parse and rewrite the structured roadmap markdown file."""

    HEADER_PATTERN = re.compile(r"^# Roadmap(?: — Project (?P<project>.+))?$", re.MULTILINE)
    TASK_SPLIT_PATTERN = re.compile(r"^### Task (?P<id>[^\s]+) — (?P<title>.+)$", re.MULTILINE)
    BULLET_PATTERN = re.compile(r"^- \*\*(?P<key>[^*]+)\*\*: ?(?P<value>.*)$")
    CHECKLIST_PATTERN = re.compile(r"^[-*] \[[ xX]\] (?P<value>.+)$")

    def parse(self, markdown_text: str) -> RoadmapDocument:
        project = self._parse_project(markdown_text)
        tasks = self.parse_tasks(markdown_text)
        return RoadmapDocument(project=project, tasks=tasks)

    def parse_tasks(self, markdown_text: str) -> list[TaskInfo]:
        matches = list(self.TASK_SPLIT_PATTERN.finditer(markdown_text))
        tasks: list[TaskInfo] = []
        for index, match in enumerate(matches):
            block_start = match.end()
            block_end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown_text)
            block = markdown_text[block_start:block_end]
            tasks.append(self._parse_task_block(match.group("id"), match.group("title"), block))

        self.validate_dependency_graph(tasks)
        return tasks

    def parse_file(self, path: Path) -> RoadmapDocument:
        return self.parse(Path(path).read_text(encoding="utf-8"))

    def render(self, document: RoadmapDocument) -> str:
        lines = [f"# Roadmap — Project {document.project}", ""]
        for task in document.tasks:
            prompt_lines = self._render_prompt_lines(task.prompt)
            lines.extend(
                [
                    f"### Task {task.id} — {task.title}",
                    f"- **Status**: {task.status.value}",
                    f"- **Priority**: {self._format_priority(task.priority)}",
                    f"- **Dependencies**: {', '.join(task.dependencies) if task.dependencies else 'none'}",
                    f"- **Skills**: {', '.join(task.skills) if task.skills else 'none'}",
                    f"- **Branch**: {task.branch or ''}",
                    f"- **Retry Count**: {task.retry_count}",
                    f"- **Max Retries**: {task.max_retries}",
                ]
            )
            lines.extend(prompt_lines)
            lines.extend(["", "**Acceptance Criteria**:"])
            if task.acceptance_criteria:
                lines.extend(f"- [ ] {criterion}" for criterion in task.acceptance_criteria)
            else:
                lines.append("- [ ] Define acceptance criteria")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def write(self, path: Path, document: RoadmapDocument) -> None:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(destination, self.render(document))

    def update_task_status(self, path: Path, task_id: str, status: TaskStatus) -> RoadmapDocument:
        document = self.parse_file(path)
        for task in document.tasks:
            if task.id == task_id:
                if task.status is not status:
                    task.transition_to(status)
                self.write(path, document)
                return document
        raise KeyError(f"Task not found in roadmap: {task_id}")

    def validate_dependency_graph(self, tasks: list[TaskInfo]) -> None:
        known_ids = {task.id for task in tasks}
        if len(known_ids) != len(tasks):
            raise ValueError("Roadmap contains duplicate task ids")

        for task in tasks:
            missing = [dependency for dependency in task.dependencies if dependency not in known_ids]
            if missing:
                raise ValueError(f"Task {task.id} depends on unknown task(s): {', '.join(missing)}")

        visiting: set[str] = set()
        visited: set[str] = set()
        task_map = {task.id: task for task in tasks}

        def visit(task_id: str) -> None:
            if task_id in visited:
                return
            if task_id in visiting:
                raise ValueError(f"Roadmap dependency graph contains a cycle involving {task_id}")
            visiting.add(task_id)
            for dependency_id in task_map[task_id].dependencies:
                visit(dependency_id)
            visiting.remove(task_id)
            visited.add(task_id)

        for task in tasks:
            visit(task.id)

    def build_task_prompt(
        self,
        task: TaskInfo,
        consensus: ConsensusDocument,
        *,
        additional_context: str = "",
        skill_contents: str | list[str] | None = None,
    ) -> str:
        context_parts = [
            f"Consensus Status: {consensus.status.value}",
            f"Consensus Version: {consensus.version}",
        ]
        if consensus.context.strip():
            context_parts.append(f"Consensus Context:\n{consensus.context.strip()}")
        if task.prompt:
            context_parts.append(f"Task Notes:\n{task.prompt}")
        if additional_context.strip():
            context_parts.append(additional_context.strip())

        if isinstance(skill_contents, list):
            rendered_skills = "\n\n".join(item.strip() for item in skill_contents if item.strip())
        elif isinstance(skill_contents, str):
            rendered_skills = skill_contents.strip()
        elif task.skills:
            rendered_skills = "\n".join(f"- {skill}" for skill in task.skills)
        else:
            rendered_skills = "No additional skills loaded."

        branch = task.branch or f"vibrant/{task.id}"
        return build_task_execution_prompt(
            project=consensus.project,
            task_title=task.title,
            acceptance_criteria=task.acceptance_criteria,
            context_sections=context_parts,
            skills_text=rendered_skills,
            branch=branch,
            task_id=task.id,
        )

    def _parse_project(self, markdown_text: str) -> str:
        match = self.HEADER_PATTERN.search(markdown_text)
        if match is None:
            return "Vibrant"
        return match.group("project") or "Vibrant"

    def _parse_task_block(self, task_id: str, title: str, block: str) -> TaskInfo:
        metadata: dict[str, str] = {}
        acceptance_criteria: list[str] = []
        prompt_lines: list[str] = []
        section: str | None = None
        inline_prompt_open = False

        for raw_line in block.splitlines():
            line = raw_line.rstrip()
            stripped = line.strip()
            if not stripped:
                if section == "prompt" and prompt_lines and prompt_lines[-1] != "":
                    prompt_lines.append("")
                elif inline_prompt_open and prompt_lines and prompt_lines[-1] != "":
                    prompt_lines.append("")
                continue

            bullet_match = self.BULLET_PATTERN.fullmatch(stripped)
            checklist_match = self.CHECKLIST_PATTERN.fullmatch(stripped)

            if stripped in {"**Acceptance Criteria**:", "#### Acceptance Criteria"}:
                section = "acceptance"
                inline_prompt_open = False
                continue
            if stripped in {"**Prompt**:", "#### Prompt"}:
                section = "prompt"
                inline_prompt_open = False
                continue
            if bullet_match is not None and section is None:
                key = bullet_match.group("key").strip()
                value = bullet_match.group("value").strip()
                metadata[key] = value
                inline_prompt_open = key == "Prompt"
                continue
            if checklist_match is not None and section == "acceptance":
                acceptance_criteria.append(checklist_match.group("value").strip())
                continue
            if section == "prompt":
                prompt_lines.append(stripped)
                continue
            if inline_prompt_open:
                prompt_lines.append(stripped)

        prompt_value = metadata.get("Prompt", "")
        if prompt_lines:
            prompt_parts = [prompt_value] if prompt_value else []
            prompt_parts.extend(prompt_lines)
            prompt_value = "\n".join(prompt_parts).strip()

        task = TaskInfo(
            id=task_id,
            title=title.strip(),
            status=self._parse_status(metadata.get("Status", TaskStatus.PENDING.value)),
            priority=self._parse_priority(metadata.get("Priority")),
            dependencies=self._parse_csv_list(metadata.get("Dependencies", "")),
            skills=self._parse_csv_list(metadata.get("Skills", "")),
            branch=metadata.get("Branch") or None,
            retry_count=self._parse_int(metadata.get("Retry Count"), default=0),
            max_retries=self._parse_int(metadata.get("Max Retries"), default=3),
            prompt=prompt_value or None,
            acceptance_criteria=acceptance_criteria,
        )
        return task

    def _parse_status(self, value: str) -> TaskStatus:
        normalized = value.strip().lower()
        return TaskStatus(normalized)

    def _parse_priority(self, value: str | None) -> int | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if not normalized or normalized == "none":
            return None
        if normalized in PRIORITY_NAME_TO_VALUE:
            return PRIORITY_NAME_TO_VALUE[normalized]
        try:
            return int(normalized)
        except ValueError as exc:
            raise ValueError(f"Invalid roadmap priority: {value}") from exc

    def _parse_csv_list(self, value: str) -> list[str]:
        normalized = value.strip()
        if not normalized or normalized.lower() == "none":
            return []
        return [item.strip() for item in normalized.split(",") if item.strip()]

    def _parse_int(self, value: str | None, *, default: int) -> int:
        if value is None:
            return default
        normalized = value.strip()
        if not normalized:
            return default
        return int(normalized)

    def _format_priority(self, value: int | None) -> str:
        if value is None:
            return "none"
        return VALUE_TO_PRIORITY_NAME.get(value, str(value))

    def _render_prompt_lines(self, prompt: str | None) -> list[str]:
        if not prompt:
            return ["- **Prompt**: "]

        normalized = prompt.rstrip("\n")
        if "\n" not in normalized:
            return [f"- **Prompt**: {normalized}"]

        return ["**Prompt**:", *normalized.splitlines()]



def _atomic_write_text(path: Path, content: str) -> None:
    descriptor, temp_path = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise
