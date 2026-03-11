"""Prompt-building service."""

from __future__ import annotations

from pathlib import Path

from vibrant.consensus import RoadmapParser
from vibrant.models.task import TaskInfo
from vibrant.orchestrator.git_manager import GitWorktreeInfo

from ..artifacts.consensus import ConsensusService


class PromptService:
    """Build execution prompts and load supporting skill contents."""

    def __init__(
        self,
        *,
        skills_dir: str | Path,
        roadmap_parser: RoadmapParser,
        consensus_service: ConsensusService,
    ) -> None:
        self.skills_dir = Path(skills_dir)
        self.roadmap_parser = roadmap_parser
        self.consensus_service = consensus_service

    def build_task_prompt(self, task: TaskInfo, worktree: GitWorktreeInfo) -> str:
        consensus = self.consensus_service.load()
        additional_context = "\n".join(
            [
                f"Working Directory: {worktree.path}",
                f"Retry Attempt: {task.retry_count + 1} of {task.max_retries + 1}",
            ]
        )
        skill_contents = self.load_task_skills(task.skills)
        return self.roadmap_parser.build_task_prompt(
            task,
            consensus,
            additional_context=additional_context,
            skill_contents=skill_contents,
        )

    def load_task_skills(self, skills: list[str]) -> list[str] | None:
        rendered: list[str] = []
        for skill in skills:
            for candidate in _skill_candidates(self.skills_dir, skill):
                if candidate.exists() and candidate.is_file():
                    rendered.append(candidate.read_text(encoding="utf-8").strip())
                    break
        return rendered or None


def _skill_candidates(skills_dir: Path, skill: str) -> tuple[Path, ...]:
    return (
        skills_dir / skill,
        skills_dir / f"{skill}.md",
        skills_dir / skill / "SKILL.md",
    )
