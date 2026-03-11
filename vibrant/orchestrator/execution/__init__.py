"""Task-execution orchestrator components."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "GitManager",
    "GitManagerError",
    "GitMergeResult",
    "GitWorktreeInfo",
    "GitWorkspaceService",
    "PromptService",
    "RetryPolicyService",
    "ReviewService",
    "TaskDispatcher",
    "TaskExecutionAttempt",
    "TaskExecutionService",
    "format_merge_error",
    "scoped_worktree_root",
]

_EXPORTS = {
    "TaskDispatcher": (".dispatcher", "TaskDispatcher"),
    "GitManager": (".git_manager", "GitManager"),
    "GitManagerError": (".git_manager", "GitManagerError"),
    "GitMergeResult": (".git_manager", "GitMergeResult"),
    "GitWorktreeInfo": (".git_manager", "GitWorktreeInfo"),
    "GitWorkspaceService": (".git_workspace", "GitWorkspaceService"),
    "format_merge_error": (".git_workspace", "format_merge_error"),
    "scoped_worktree_root": (".git_workspace", "scoped_worktree_root"),
    "PromptService": (".prompts", "PromptService"),
    "RetryPolicyService": (".retry_policy", "RetryPolicyService"),
    "ReviewService": (".review", "ReviewService"),
    "TaskExecutionAttempt": (".service", "TaskExecutionAttempt"),
    "TaskExecutionService": (".service", "TaskExecutionService"),
}


def __getattr__(name: str) -> Any:
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    module = import_module(module_name, __name__)
    value = getattr(module, attribute)
    globals()[name] = value
    return value
