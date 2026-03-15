# Git Worktree Redesign Plan

## Goal

Replace the current copytree-plus-sync workspace model with real git-backed isolated workspaces and merge integration. The orchestrator should treat commits and branches as the durable unit of task output, not copied directory contents.

## Current Problem

The existing workspace implementation in [vibrant/orchestrator/basic/workspace/service.py](/home/rogerw/project/vibrant/vibrant/orchestrator/basic/workspace/service.py) creates task workspaces with `shutil.copytree()` and "merges" accepted work by making the project tree match the workspace tree. That is not a real merge and can overwrite or delete unrelated changes.

## Proposed Design

### Core model

- Use real git worktrees under the configured workspace root.
- Persist workspace metadata instead of relying on in-memory handles alone.
- Record the exact base commit used to create each task workspace.
- Require a clean target repo for the first version of this redesign.

### Durable workspace metadata

Persist at least:

- `workspace_id`
- `task_id`
- `attempt_id`
- `path`
- `branch_name`
- `target_ref`
- `base_commit`
- `result_commit`
- `status`
- timestamps

This should live in a dedicated store alongside attempts and reviews.

### Task execution flow

1. Resolve the target ref from the current checked-out branch in the project repo.
2. Record `base_commit` from `HEAD`.
3. Create a task branch such as `vibrant/task/<task-id>/<attempt-id>`.
4. Create an isolated worktree directory with `git worktree add`.
5. Run the code agent inside that worktree path.
6. When the run ends, inspect the worktree with git.
7. If there are changes but no commit, create a bot-authored commit automatically.
8. Persist the resulting commit as `result_commit`.
9. If there are no changes, treat that as a distinct outcome instead of fabricating a review diff.

Worker interaction policy:

- Code, validation, merge-resolution, and other worker-style agents are autonomous.
- Provider-side interactive requests must be auto-rejected for those roles.
- This redesign must not rely on worker `awaiting_input` recovery to make task execution safe.
- User-facing clarification remains a Gatekeeper-owned workflow.

### Review flow

- Replace the fake `.vibrant-review.diff` artifact with a real diff generated from git.
- Build the review artifact from `git diff --binary <base_commit>..<result_commit>`.
- Attach commit metadata to the review ticket so review is grounded in an actual change set.

### Merge flow

On review acceptance:

1. Do not copy files into the project root.
2. Create an isolated integration worktree from the latest `target_ref`.
3. Attempt `git merge --no-ff <result_commit>` inside the integration worktree.
4. If merge succeeds, run validation there.
5. If validation passes, update the target branch and refresh the root worktree.
6. If merge conflicts, keep the integration worktree and hand it to a merge-resolution agent.
7. The merge-resolution agent resolves conflicts, commits the result, and reruns validation.
8. If merge resolution succeeds, return that integrated result to review or finalize directly depending on policy.
9. If merge resolution fails, create a merge-conflict or escalation review ticket.

## Required code changes

### Workspace layer

Refactor [vibrant/orchestrator/basic/workspace/service.py](/home/rogerw/project/vibrant/vibrant/orchestrator/basic/workspace/service.py) into a git-backed workspace manager with methods equivalent to:

- `create_task_worktree`
- `get_workspace`
- `capture_result_commit`
- `build_review_diff`
- `create_integration_worktree`
- `attempt_merge`
- `finalize_merge`
- cleanup helpers

### Execution layer

Update [vibrant/orchestrator/policy/task_loop/execution.py](/home/rogerw/project/vibrant/vibrant/orchestrator/policy/task_loop/execution.py) so successful attempts persist git result information rather than relying on raw workspace contents.

### Task loop

Update [vibrant/orchestrator/policy/task_loop/loop.py](/home/rogerw/project/vibrant/vibrant/orchestrator/policy/task_loop/loop.py) so accepting a review ticket starts a git integration flow rather than a filesystem sync.

### Persistence

Add a new workspace metadata store and extend review/attempt state as needed for:

- base commit
- result commit
- integration commit
- merge status

### Type updates

Extend merge-related result types so they can distinguish:

- `merged`
- `conflicted`
- `validation_failed`
- `dirty_target`
- `stale_target`

## Migration sequence

1. Add durable git-backed workspace metadata.
2. Switch workspace creation from directory copy to `git worktree add`.
3. Replace fake review diffs with real git diff artifacts.
4. Capture a durable `result_commit` for every successful attempt.
5. Replace acceptance-time filesystem sync with isolated git merge integration.
6. Add merge-resolution-agent handling for conflicts.
7. Remove the old copytree/sync implementation completely.

## Verification

Verify all of the following:

- A task workspace is a real git worktree on its own branch.
- Review diffs match `base_commit..result_commit`.
- Accepting one task does not delete unrelated project files.
- Two tasks started from the same base can be accepted in either order.
- The second acceptance performs a merge or conflict flow instead of overwriting the first.
- Merge conflicts can be resolved in an isolated integration worktree.
- Restart recovery works using persisted workspace metadata and git refs.

## Notes

For the first implementation, explicitly reject execution if the target repo is dirty. Handling mixed uncommitted local changes in the root worktree requires a separate design and should not be folded into the first migration.

The same "first implementation" rule applies to agent interactivity: worker attempts should fail or auto-reject rather than entering a durable human-input wait state. Gatekeeper remains the interactive authority.
