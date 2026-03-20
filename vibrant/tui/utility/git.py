from __future__ import annotations

import os
import pathlib
import subprocess

INITIAL_COMMIT_MESSAGE = "Initialize repository for Vibrant"
INITIAL_COMMIT_AUTHOR_NAME = "Vibrant"
INITIAL_COMMIT_AUTHOR_EMAIL = "vibrant@example.invalid"


def is_git_repository(path: pathlib.Path) -> bool:
    """Check if the given path is a Git repository."""
    return (path / ".git").exists()


def is_under_git_repository(path: pathlib.Path) -> bool:
    """Check if the given path is under a Git repository."""
    current_path = path.resolve()
    while current_path != current_path.parent:
        if is_git_repository(current_path):
            return True
        current_path = current_path.parent
    return False


def initialize_git_repository(path: pathlib.Path) -> None:
    """Initialize a Git repository at the given path."""
    _run_git(path, "init")


def ensure_git_repository_commit(path: pathlib.Path) -> None:
    """Create the first commit when a Git repository exists but HEAD is unborn."""

    if not _git_success(path, "rev-parse", "--is-inside-work-tree"):
        return
    if _git_success(path, "rev-parse", "--verify", "HEAD"):
        return

    _run_git(path, "add", "--all", ".")
    _run_git(
        path,
        "commit",
        "--allow-empty",
        "-m",
        INITIAL_COMMIT_MESSAGE,
        env=_initial_commit_environment(),
    )


def _initial_commit_environment() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("GIT_AUTHOR_NAME", INITIAL_COMMIT_AUTHOR_NAME)
    env.setdefault("GIT_AUTHOR_EMAIL", INITIAL_COMMIT_AUTHOR_EMAIL)
    env.setdefault("GIT_COMMITTER_NAME", env["GIT_AUTHOR_NAME"])
    env.setdefault("GIT_COMMITTER_EMAIL", env["GIT_AUTHOR_EMAIL"])
    return env


def _git_success(path: pathlib.Path, *args: str) -> bool:
    completed = subprocess.run(
        ["git", *args],
        cwd=path,
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.returncode == 0


def _run_git(path: pathlib.Path, *args: str, env: dict[str, str] | None = None) -> None:
    subprocess.run(
        ["git", *args],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
