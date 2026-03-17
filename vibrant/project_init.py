"""Project initialization helpers for ``vibrant init``."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .config import DEFAULT_CONFIG_DIR, VibrantConfig
from .consensus.writer import ConsensusWriter
from .models.consensus import ConsensusDocument, ConsensusStatus
from .orchestrator.types import GatekeeperSessionSnapshot, WorkflowStatus, utc_now

GITIGNORE_ENTRIES = [
    "*"
]

DIRECTORIES = [
    Path("skills"),
    Path("agent-instances"),
    Path("agent-runs"),
    Path("conversations"),
    Path("prompts"),
    Path("logs/providers/native"),
    Path("logs/providers/canonical"),
    Path("consensus.history"),
]


def initialize_project(target_path: Path = Path(".")) -> Path:
    """Create the ``.vibrant`` project structure if it does not already exist."""

    project_root = _normalize_project_root(target_path)
    vibrant_dir = project_root / DEFAULT_CONFIG_DIR
    vibrant_dir.mkdir(parents=True, exist_ok=True)

    for relative_dir in DIRECTORIES:
        (vibrant_dir / relative_dir).mkdir(parents=True, exist_ok=True)

    _write_if_missing(vibrant_dir / "consensus.md", _render_consensus_markdown(project_root.name))
    _write_if_missing(vibrant_dir / "roadmap.md", _render_roadmap_markdown(project_root.name))
    _write_if_missing(vibrant_dir / "vibrant.toml", _render_default_config())
    _write_if_missing(vibrant_dir / "state.json", _render_initial_state())
    _ensure_gitignore(vibrant_dir / ".gitignore")

    return vibrant_dir


def ensure_project_files(target_path: Path = Path(".")) -> Path | None:
    """Backfill missing files for an already-initialized ``.vibrant`` directory."""

    project_root = _normalize_project_root(target_path)
    vibrant_dir = project_root / DEFAULT_CONFIG_DIR
    if not vibrant_dir.exists():
        return None

    return initialize_project(project_root)


def _normalize_project_root(target_path: Path) -> Path:
    """Normalize a project path, accepting either the repo root or ``.vibrant``."""

    project_root = target_path.expanduser().resolve()
    if project_root.name == DEFAULT_CONFIG_DIR:
        return project_root.parent
    return project_root


def _write_if_missing(path: Path, content: str) -> None:
    if not path.exists():
        path.write_text(content, encoding="utf-8")


def _render_consensus_markdown(project_name: str) -> str:
    timestamp = datetime.now(timezone.utc).replace(microsecond=0)
    document = ConsensusDocument(
        project=project_name or "Vibrant",
        created_at=timestamp,
        updated_at=timestamp,
        version=0,
        status=ConsensusStatus.INIT,
    )
    return ConsensusWriter().render(document)


def _render_roadmap_markdown(project_name: str) -> str:
    return (
        f"# Roadmap — Project {project_name or 'Vibrant'}\n\n"
        "_This roadmap is intentionally empty until planning begins._\n"
    )


def _render_default_config() -> str:
    config = VibrantConfig()
    lines = [
        "[provider]",
        f'kind = "{config.provider_kind.value}"',
        f'codex-binary = "{config.codex_binary}"',
        "launch-args = []",
        f'model = "{config.model}"',
        f'approval-policy = "{config.approval_policy}"',
        f'reasoning-effort = "{config.reasoning_effort}"',
        f'reasoning-summary = "{config.reasoning_summary}"',
        f'sandbox-mode = "{config.sandbox_mode}"',
        "",
        "[orchestrator]",
        f"concurrency-limit = {config.concurrency_limit}",
        f"agent-timeout-seconds = {config.agent_timeout_seconds}",
        f'worktree-directory = "{config.worktree_directory}"',
        f'conversation-directory = "{config.conversation_directory}"',
        f'execution-mode = "{config.execution_mode.value}"',
        "",
        "[validation]",
        "test-commands = []",
        "",
    ]
    if config.model_provider is not None:
        lines.insert(4, f'model-provider = "{config.model_provider}"')
    return "\n".join(lines)


def _render_initial_state() -> str:
    gatekeeper_session = GatekeeperSessionSnapshot()
    gatekeeper_payload = {
        **asdict(gatekeeper_session),
        "lifecycle_state": gatekeeper_session.lifecycle_state.value,
    }
    gatekeeper_payload.pop("provider_thread_id", None)
    state = {
        "session_id": str(uuid4()),
        "started_at": utc_now(),
        "workflow_status": WorkflowStatus.INIT.value,
        "resume_status": None,
        "concurrency_limit": 4,
        "gatekeeper_session": gatekeeper_payload,
        "total_agent_spawns": 0,
    }
    import json

    return json.dumps(state, indent=2) + "\n"


def _ensure_gitignore(path: Path) -> None:
    existing_lines: list[str] = []
    if path.exists():
        existing_lines = path.read_text(encoding="utf-8").splitlines()

    final_lines = list(existing_lines)
    for entry in GITIGNORE_ENTRIES:
        if entry not in final_lines:
            final_lines.append(entry)

    content = "\n".join(final_lines).rstrip() + "\n"
    path.write_text(content, encoding="utf-8")
