# Backend Policy Loops E2E Plan

## Goal

Prove the real backend policy loops end to end with a real `.vibrant` project fixture, real stores, real conversations, real git worktrees, real review diffs, and real merge behavior.

The only fake component should be the provider implementation.

This plan covers:

- Gatekeeper submission and pending-question resolution
- Task dispatch, attempt execution, review ticket creation, restart recovery, retry, and merge acceptance
- Durable artifacts under `.vibrant/`
- Manual inspection of logs, diffs, conversations, and workspace state

This plan does not attempt to prove real-model reasoning quality.

## Principles

### Keep the backend real

Do not stub or monkeypatch:

- `TaskLoop`
- `ExecutionCoordinator`
- `GatekeeperLifecycleService`
- `WorkspaceService`
- review/diff/merge paths

Use the real composition root in [vibrant/orchestrator/bootstrap.py](/home/rogerw/project/vibrant/vibrant/orchestrator/bootstrap.py#L82).

### Fake only the provider

Use a deterministic fixture provider injected through the normal adapter hooks. It must behave like a provider, not like a test backdoor.

### Prompt-driven, not harness-driven

The provider should receive the full normal prompt text and also see in-band mock markers. Markers should only force deterministic behavior shape. They must not replace the meaning of the prompt.

Good pattern:

```text
Update `demo.txt` so it contains `workspace-change`.
Leave enough evidence in logs for review.
[mock:write demo.txt]
[mock:content workspace-change]
[mock:tool]
```

That keeps the test meaningful if the mock is later replaced by a real provider.

### Artifacts matter as much as assertions

Assertions are only half the test. The other half is manual inspection of:

- provider logs
- conversations
- attempts
- review tickets
- diffs
- workspaces
- root repo state before and after acceptance

## Current Code Constraints

The `.vibrant` project layout already supports the artifact types we need in [vibrant/project_init.py](/home/rogerw/project/vibrant/vibrant/project_init.py#L36).

The current task-loop path already performs the right durable backend actions:

- dispatch and attempt start in [vibrant/orchestrator/policy/task_loop/attempts.py](/home/rogerw/project/vibrant/vibrant/orchestrator/policy/task_loop/attempts.py#L23)
- runtime-backed execution in [vibrant/orchestrator/policy/task_loop/execution.py](/home/rogerw/project/vibrant/vibrant/orchestrator/policy/task_loop/execution.py#L79)
- review-ticket creation and merge resolution in [vibrant/orchestrator/policy/task_loop/reviews.py](/home/rogerw/project/vibrant/vibrant/orchestrator/policy/task_loop/reviews.py#L60)
- git-backed result capture and merge in [vibrant/orchestrator/basic/workspace/service.py](/home/rogerw/project/vibrant/vibrant/orchestrator/basic/workspace/service.py#L47)
- durable conversation frames in [vibrant/orchestrator/basic/conversation/store.py](/home/rogerw/project/vibrant/vibrant/orchestrator/basic/conversation/store.py#L23)

The main gap is the current mock adapter. It emits canonical events through callbacks but does not write normal provider NDJSON logs, so it is not sufficient for backend e2e artifact inspection in [vibrant/providers/mock/adapter.py](/home/rogerw/project/vibrant/vibrant/providers/mock/adapter.py#L318).

Also note that validation is still synthetic. The task loop enters validating and then falls back to a default "Validation not configured yet." result in [vibrant/orchestrator/policy/task_loop/attempts.py](/home/rogerw/project/vibrant/vibrant/orchestrator/policy/task_loop/attempts.py#L153). This e2e plan proves current backend behavior honestly; it does not pretend that real validator execution already exists.

## Recommended Harness Design

### New test-side files

- `tests/e2e/test_policy_loops_e2e.py`
- `tests/e2e/fixture_provider.py`
- `tests/e2e/artifacts.py`
- `tests/e2e/conftest.py` if shared fixtures become large

### Fixture project

Each test should create a real project fixture that:

1. Initializes `.vibrant`
2. Initializes a git repo
3. Creates a tracked baseline file such as `demo.txt`
4. Writes a config override so worktrees land inside the artifact root instead of the default `/tmp/vibrant-worktrees`
5. Sets `concurrency-limit = 1` for deterministic task ordering
6. Preserves `.vibrant/conversations`, `.vibrant/logs`, and review diffs for later inspection

### Stable artifact root

Use an env var such as `VIBRANT_E2E_ARTIFACT_ROOT`.

If set:

- create one stable artifact directory per test
- keep the full project there
- never delete it at test end
- write an `artifact-manifest.json` with all important ids and file paths

If not set:

- use `tmp_path` normally

### Orchestrator construction

Construct the orchestrator normally, but inject the fixture provider for both paths:

- `gatekeeper=Gatekeeper(project_root, adapter_factory=FixtureProviderAdapter)`
- `adapter_factory=FixtureProviderAdapter`

That keeps both Gatekeeper and worker runs on the same deterministic provider model.

## Fixture Provider Requirements

The fixture provider should be deterministic, prompt-driven, side-effect-capable, and log-writing.

### Required capabilities

- emit normal canonical lifecycle events:
  - `session.started`
  - `thread.started`
  - `turn.started`
  - reasoning deltas
  - optional tool/request events
  - content deltas
  - assistant completion
  - `turn.completed` or `runtime.error`
- persist native and canonical NDJSON logs to the run-record paths
- persist `provider_thread_id` and `resume_cursor`
- support resumed threads
- optionally write or append files relative to `cwd`
- optionally emit `request.opened` and wait for `respond_to_request`
- optionally fail with `runtime.error`

### Suggested marker set

- `[mock:write demo.txt]`
- `[mock:append demo.txt]`
- `[mock:content workspace-change]`
- `[mock:tool]`
- `[mock:question]`
- `[mock:error]`
- `[mock:long]`

The provider should parse the full prompt and act on both normal content and markers. Markers are for determinism, not semantics replacement.

### Role-aware behavior

Worker runs:

- may write files in `cwd`
- may emit tool-call events
- may fail
- must still be subject to worker interactive-input policy

Gatekeeper runs:

- must remain read-only
- may emit `request.opened`
- should support resumed threads and follow-up conversations

## Test Matrix

### 1. Gatekeeper Question Resolution E2E

Purpose:

- prove the Gatekeeper user loop
- prove pending-question routing and answer resolution
- prove provider awaiting-input flow and durable conversation/log artifacts

Flow:

1. Create a real project fixture
2. Create the orchestrator
3. Create a blocking pending question through the normal command surface
4. Submit user input through `control_plane.submit_user_input(...)`
5. Let the provider emit `request.opened`, wait for the response path, then complete
6. Wait for the Gatekeeper submission result

Assertions:

- the question stays pending until the submission completes
- the question resolves after `wait_for_gatekeeper_submission(...)`
- Gatekeeper session moves through starting/running/awaiting-user/idle
- the Gatekeeper run record exists and points to provider logs
- conversation frames exist for the Gatekeeper conversation

Manual inspection:

- canonical log shows `request.opened` then `request.resolved`
- conversation frames show the user message and system status frames
- `.vibrant/questions.json` shows the resolved answer

Relevant code:

- [vibrant/orchestrator/policy/gatekeeper_loop/submission.py](/home/rogerw/project/vibrant/vibrant/orchestrator/policy/gatekeeper_loop/submission.py#L18)
- [vibrant/orchestrator/policy/gatekeeper_loop/lifecycle.py](/home/rogerw/project/vibrant/vibrant/orchestrator/policy/gatekeeper_loop/lifecycle.py#L102)

### 2. Task Loop Happy Path: Review, Restart, Accept

Purpose:

- prove real dispatch
- prove workspace edits in a real worktree
- prove result capture and review diff generation
- prove pending review survives orchestrator restart
- prove acceptance merges changes back into the root repo

Flow:

1. Create a real project fixture with tracked `demo.txt`
2. Add one task through the control plane
3. Put natural-language instructions and deterministic markers in `task.prompt`
4. End planning or set workflow to executing
5. Run `run_until_blocked()`
6. Verify the task stops at `review_pending`
7. Recreate the orchestrator from disk
8. Accept the pending review ticket
9. Verify root repo content is updated only after acceptance

Assertions:

- `run_until_blocked()` returns `review_pending`
- exactly one pending review ticket exists
- the ticket has `base_commit`, `result_commit`, and `diff_ref`
- the diff file contains the expected git diff for `demo.txt`
- the restarted orchestrator can still resolve the ticket
- accepting the ticket changes root `demo.txt`
- the task ends accepted
- the attempt ends accepted

Manual inspection:

- `.vibrant/review-diffs/*.diff`
- `.vibrant/workspaces.json`
- `.vibrant/attempts.json`
- `.vibrant/reviews.json`
- worker native and canonical logs
- root git log before and after acceptance

Relevant code:

- [vibrant/orchestrator/policy/task_loop/attempts.py](/home/rogerw/project/vibrant/vibrant/orchestrator/policy/task_loop/attempts.py#L23)
- [vibrant/orchestrator/policy/task_loop/execution.py](/home/rogerw/project/vibrant/vibrant/orchestrator/policy/task_loop/execution.py#L79)
- [vibrant/orchestrator/policy/task_loop/reviews.py](/home/rogerw/project/vibrant/vibrant/orchestrator/policy/task_loop/reviews.py#L73)
- [vibrant/orchestrator/basic/workspace/service.py](/home/rogerw/project/vibrant/vibrant/orchestrator/basic/workspace/service.py#L95)

### 3. Task Loop Retry Cycle E2E

Purpose:

- prove retry review resolution
- prove task requeue behavior
- prove a second attempt gets a fresh run and fresh review artifact

Flow:

1. Run one task to `review_pending`
2. Call `retry_review_ticket(...)` through the control plane
3. Optionally patch the prompt so the second attempt produces a distinct result
4. Run the task loop again
5. Confirm a new review ticket appears
6. Accept the second review ticket

Assertions:

- the first ticket resolves as retry
- the task re-enters queued/active flow and `retry_count` increments
- the second attempt has a different attempt id and run id
- the second review ticket is distinct
- final acceptance merges the second attempt result into the root repo

Manual inspection:

- both attempts in `.vibrant/attempts.json`
- both run records in `.vibrant/agent-runs/`
- review history shows retry then accept
- both canonical logs are preserved

Relevant code:

- [vibrant/orchestrator/policy/task_loop/reviews.py](/home/rogerw/project/vibrant/vibrant/orchestrator/policy/task_loop/reviews.py#L28)

### 4. Worker Interactive Request Rejection E2E

Purpose:

- prove that worker runs cannot wait for interactive input
- prove the worker request is converted into a failed attempt

Flow:

1. Add a task whose prompt causes the provider to emit `request.opened`
2. Run the task loop
3. Let the worker runtime auto-reject the request

Assertions:

- the result is failed
- the attempt status is failed
- the task becomes blocked or failed according to current policy
- no review ticket is created
- the worker canonical log contains the request event and failure evidence

Manual inspection:

- worker canonical log shows the request event
- `.vibrant/attempts.json` contains the failure
- no review diff is created

## Assertion Strategy

Assert durable backend facts, not transcript style.

### Assert these

- task, attempt, question, and workflow status transitions
- presence and content of review diffs
- presence of provider log files
- presence of conversation frames
- persistence across orchestrator restart
- root repo content changes only after review acceptance
- workspace metadata and review metadata are consistent

### Avoid asserting these

- full transcript wording
- exact chunk boundaries
- exact reasoning wording
- exact event counts beyond key milestones

## Artifact Checklist

Every test should either preserve or summarize these paths:

- `.vibrant/attempts.json`
- `.vibrant/questions.json`
- `.vibrant/reviews.json`
- `.vibrant/workspaces.json`
- `.vibrant/agent-runs/*.json`
- `.vibrant/logs/providers/native/*.ndjson`
- `.vibrant/logs/providers/canonical/*.ndjson`
- `.vibrant/conversations/index.json`
- `.vibrant/conversations/frames/*.jsonl`
- `.vibrant/review-diffs/*.diff`
- root repo files
- root repo git history
- task worktree directories

Recommended `artifact-manifest.json` fields:

- `test_name`
- `project_root`
- `artifact_root`
- `run_ids`
- `conversation_ids`
- `question_ids`
- `attempt_ids`
- `review_ticket_ids`
- `workspace_ids`
- `diff_paths`
- `expected_manual_checks`

## Implementation Order

1. Add `tests/e2e/fixture_provider.py`
2. Implement NDJSON logging in that provider
3. Implement deterministic file-edit behavior in that provider
4. Implement deterministic `tool`, `question`, `error`, and `long` behaviors
5. Add `tests/e2e/artifacts.py` for artifact-root creation and manifest writing
6. Add a project fixture with real git setup and config overrides
7. Add an orchestrator fixture that injects the fixture provider into both Gatekeeper and worker paths
8. Implement `gatekeeper_question_resolution_e2e`
9. Implement `task_loop_happy_path_review_restart_accept_e2e`
10. Implement `task_loop_retry_cycle_e2e`
11. Implement `task_loop_worker_request_is_rejected_e2e`
12. Add a short module docstring explaining how to run the suite and where artifacts are preserved

## Verification

Run the focused e2e suite:

```bash
VIBRANT_E2E_ARTIFACT_ROOT=/tmp/vibrant-e2e uv run pytest tests/e2e/test_policy_loops_e2e.py -q -s
```

Run the main happy-path case only:

```bash
VIBRANT_E2E_ARTIFACT_ROOT=/tmp/vibrant-e2e uv run pytest tests/e2e/test_policy_loops_e2e.py -k happy_path -q -s
```

Success means:

- tests pass without monkeypatching core policy services
- a full artifact bundle is produced
- manual inspection shows coherent logs, conversations, attempts, review tickets, diffs, and merges
- swapping the fixture provider for a real provider would not require rewriting the test logic, only changing provider selection
