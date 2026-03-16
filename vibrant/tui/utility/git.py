from __future__ import annotations

import pathlib
import subprocess


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
    subprocess.run(
        ["git", "init"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
