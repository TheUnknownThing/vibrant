# Debug Findings — 2026-03-15

This document rechecks the original 2026-03-15 debugging notes against the current tree.

## Status Summary

- No action: 1, 8
- Fixed or obsolete: 4, 9
- Still open: 2, 3, 5, 6, 7
- Reframed: 3 and 7 are still real, but the root cause is no longer copied workspaces. The current implementation uses git worktrees and intentionally excludes `.vibrant` from durable task results.

## 1. Gatekeeper MCP startup failed because loopback MCP traffic was sent through the configured HTTP proxy

### Current status

No product fix planned.

### Recheck

This still matches the code shape:

- `vibrant/providers/codex/client.py` launches Codex with the inherited environment.
- If `HTTP_PROXY` or `HTTPS_PROXY` is set and `NO_PROXY` does not include `127.0.0.1` and `localhost`, loopback MCP traffic can still be misrouted.

### Assessment

This is an environment issue, not an orchestrator defect.

### Plan

Optional only: document the `NO_PROXY=127.0.0.1,localhost` prerequisite in troubleshooting docs.

## 2. Worker tasks cannot durably mutate orchestrator-owned state

### Current status

Open.

### Recheck

This is still true:

- `vibrant/orchestrator/policy/shared/capabilities.py` gives workers only `READ_SCOPE`.
- Gatekeeper-only scopes still protect roadmap, consensus, workflow, question, and review writes.

The March wording should be tightened: the architectural restriction is correct, but worker-side prompts still need to make that boundary explicit.

### Impact

If a task prompt tells a worker to update roadmap, consensus, workflow, or review state directly, the instruction is unsound. At best the worker can edit local files in its task worktree; it cannot apply durable orchestrator mutations.

### Fix plan

1. Update the worker prompt to state explicitly that `.vibrant` and orchestrator state are read-only from the worker's perspective.
2. Tell workers to report proposed roadmap or consensus changes in their summary instead of editing orchestrator files.
3. Add a regression test that verifies worker prompts do not instruct durable orchestrator writes.

## 3. `.vibrant` edits inside task worktrees are still non-durable

### Current status

Open, but the original cause is stale.

### Recheck

The old copytree analysis is obsolete. The current workspace service now provisions real git worktrees:

- `vibrant/orchestrator/basic/workspace/service.py` uses `git worktree add`.

However, `.vibrant` is still intentionally excluded from task-result capture and target-repo cleanliness checks:

- `_EXCLUDED_PATHS = (".vibrant", ".vibrant/**")`
- `_excluded_pathspec()` is used by status, add, and restore operations.

So a worker can still edit `.vibrant` locally, but those edits are ignored when result commits and review diffs are produced.

### Impact

Local `.vibrant` edits can mislead the worker and pollute summaries, but they are not part of the authoritative result.

### Fix plan

1. Detect `.vibrant` edits at the end of a task run and fail or warn loudly instead of silently ignoring them.
2. Reset `.vibrant` in the task worktree before review handoff so summaries are less likely to reflect non-authoritative state.
3. Longer term, consider preventing worker-visible `.vibrant` writes entirely if the worktree model allows it cleanly.

## 4. The code-agent prompt claimed a real git worktree existed, but the workspace service only created a copied directory

### Current status

Fixed.

### Recheck

The prompt says the worker is in a git worktree, and that is now true:

- `vibrant/prompts/code_agent.py` tells the worker it is in a git worktree and should commit.
- `vibrant/orchestrator/basic/workspace/service.py` now creates real git worktrees and captures commits from them.

### Assessment

This finding was valid for the older implementation but is now obsolete.

## 5. Consensus decision appends can still create malformed duplicate `Design Choices` sections

### Current status

Open.

### Recheck

This code path still exists:

- `vibrant/models/consensus.py` still defines `DECISIONS` markers in `DEFAULT_CONSENSUS_CONTEXT`.
- `vibrant/orchestrator/basic/stores/consensus.py` still uses `_append_to_decisions()`.
- If the marker block is missing, `_append_to_decisions()` still appends a brand new `## Design Choices` section instead of failing closed.

The original concern remains valid: this fallback can silently duplicate canonical structure.

### Recommended direction

Prefer removing the marker-based decision append path entirely. The current consensus model already treats the body as raw markdown plus metadata, and the structured decision markers are not buying enough safety to justify this repair logic.

### Fix plan

1. Remove `append_decision()` and the `DECISIONS` marker contract from the consensus store and MCP surface.
2. Treat consensus body updates as whole-document writes through `write_consensus_document()` or `update_consensus(context=...)`.
3. Update `DEFAULT_CONSENSUS_CONTEXT` and consensus tests to stop depending on decision markers.
4. If the removal must be staged, first change `_append_to_decisions()` to raise an error when the marker block is missing instead of appending a duplicate section.

## 6. Run summaries and review ticket summaries still double-count assistant output

### Current status

Open.

### Recheck

This still matches the current implementation:

- `vibrant/agents/base.py` appends transcript text from both `content.delta` and `task.progress`.
- `vibrant/providers/codex/adapter.py` still emits assistant-visible content through both channels in different forms.

That means a final summary can still duplicate assistant text.

### Impact

These summaries remain unreliable:

- `agent_record.outcome.summary`
- review ticket summaries
- any UI that renders them directly

### Fix plan

1. Build transcript summaries from `content.delta` only.
2. Keep `task.progress` for UI progress and logs, not summary assembly.
3. Add regression coverage for Codex runs where the same assistant text appears in both event streams.

## 7. Worker summaries can still describe local `.vibrant` state as if it were authoritative

### Current status

Open, and downstream of 2 and 3.

### Recheck

This risk still exists even with real git worktrees:

- the worker can see and edit local `.vibrant` files in its task worktree
- those edits are excluded from durable capture
- the worker summary is free text and can still describe those local changes as if they became project state

### Impact

Review tickets can contain technically false claims about roadmap, consensus, or workflow state.

### Fix plan

1. Fix 2 and 3 first.
2. Add a guard that warns when a task summary mentions orchestrator-state changes after local `.vibrant` edits were detected.
3. Prefer review prompts and UI copy that treat worker summaries as non-authoritative narrative, not state truth.

## 8. `workflow_status = executing` while an attempt is `review_pending` is intentional

### Current status

No action.

### Recheck

The type model still separates:

- coarse workflow status such as `executing`
- finer task-loop or attempt status such as `review_pending`

Relevant code still supports this split:

- `vibrant/orchestrator/types.py`
- `vibrant/orchestrator/policy/task_loop/models.py`
- `vibrant/orchestrator/policy/task_loop/loop.py`

### Assessment

This is intended modeling, not a defect.

## 9. Review diffs are placeholders

### Current status

Fixed.

### Recheck

This is no longer true:

- `vibrant/orchestrator/basic/workspace/service.py` now writes a real binary git diff for `base_commit..result_commit`.
- The review ticket stores the produced diff path.

### Assessment

The placeholder finding is obsolete after the git-worktree redesign.

## Recommended Fix Order

1. Fix transcript assembly in `AgentBase.run()` so summaries and review tickets become trustworthy again.
2. Harden the worker/task boundary around `.vibrant` and orchestrator-owned state.
3. Remove or fail-close the consensus decision-marker append path.
4. Optionally add operator docs for the `NO_PROXY` requirement.
