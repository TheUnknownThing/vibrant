# Orchestrator Storage Refactor

## Summary

This refactor moves durable agent storage and access out of the implicit
`OrchestratorEngine.agents` cache and into an explicit file-backed store on the
orchestrator side.

The `vibrant/agents/*` runtime and protocol code is intentionally unchanged.

## What Changed

- Added `AgentRecordStore` in `vibrant/orchestrator/agents/store.py`
- Refactored `AgentRegistry` to read/write agent records through that store
- Updated `StateStore` to coordinate Gatekeeper result persistence with the
  agent store instead of always routing through `OrchestratorEngine`
- Updated `QuestionService.answer()` to use orchestrator artifacts/state components instead of
  calling `engine.answer_pending_question()` directly
- Updated lifecycle/facade/TUI compatibility reads to prefer orchestrator
  domain packages over the engine's in-memory `agents` dict

## New Ownership Model

### Authoritative stores

- `state.json` / `StateStore`
  - workflow/session state
  - gatekeeper status
  - cross-cutting counters such as `total_agent_spawns`
- `.vibrant/agents/*.json` / `AgentRecordStore`
  - durable per-agent records
  - provider thread metadata
  - latest persisted agent summaries/errors/status

### Compatibility projection

`OrchestratorEngine.agents` is now treated as a mirrored compatibility cache for
legacy surfaces. The source of truth is the per-agent JSON file set.

## Why This Helps

- Removes agent persistence ownership from the workflow engine
- Makes per-agent JSON files the explicit durable source of truth
- Lets higher-level services query agent records without coupling to engine
  internals
- Keeps the current TUI/facade working by mirroring the store back into the
  engine cache during the transition

## Files Touched

- `vibrant/orchestrator/agents/store.py`
- `vibrant/orchestrator/agents/registry.py`
- `vibrant/orchestrator/state/store.py`
- `vibrant/orchestrator/artifacts/questions.py`
- `vibrant/orchestrator/lifecycle.py`
- `vibrant/orchestrator/agents/__init__.py`, `vibrant/orchestrator/artifacts/__init__.py`, `vibrant/orchestrator/execution/__init__.py`, and `vibrant/orchestrator/state/__init__.py`
- `vibrant/orchestrator/facade.py`
- `vibrant/tui/app.py`

## Follow-up Work

- Split question storage into its own durable store instead of keeping it in
  `state.json`
- Move more derived workflow projections out of `state.json`
- Replace remaining engine-based compatibility paths with service/query reads
- Shrink `OrchestratorEngine` to workflow-state persistence and migration only
