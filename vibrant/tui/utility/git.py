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
        "--no-verify",
        "--no-gpg-sign",
        "--allow-empty",
        "-m",
        INITIAL_COMMIT_MESSAGE,
        env=_initial_commit_environment(path),
    )


def _initial_commit_environment(path: pathlib.Path) -> dict[str, str]:
    env = os.environ.copy()
    identity = {
        "GIT_AUTHOR_NAME": env.get("GIT_AUTHOR_NAME") or _git_config(path, "user.name"),
        "GIT_AUTHOR_EMAIL": env.get("GIT_AUTHOR_EMAIL") or _git_config(path, "user.email"),
        "GIT_COMMITTER_NAME": env.get("GIT_COMMITTER_NAME"),
        "GIT_COMMITTER_EMAIL": env.get("GIT_COMMITTER_EMAIL"),
    }

    identity["GIT_AUTHOR_NAME"] = identity["GIT_AUTHOR_NAME"] or INITIAL_COMMIT_AUTHOR_NAME
    identity["GIT_AUTHOR_EMAIL"] = identity["GIT_AUTHOR_EMAIL"] or INITIAL_COMMIT_AUTHOR_EMAIL
    identity["GIT_COMMITTER_NAME"] = identity["GIT_COMMITTER_NAME"] or identity["GIT_AUTHOR_NAME"]
    identity["GIT_COMMITTER_EMAIL"] = identity["GIT_COMMITTER_EMAIL"] or identity["GIT_AUTHOR_EMAIL"]
    env.update(identity)
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


def _git_config(path: pathlib.Path, key: str) -> str | None:
    completed = subprocess.run(
        ["git", "config", "--get", key],
        cwd=path,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip()
    return value or None


def _run_git(path: pathlib.Path, *args: str, env: dict[str, str] | None = None) -> None:
    subprocess.run(
        ["git", *args],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
