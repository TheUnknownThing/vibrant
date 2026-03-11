# Orchestrator Storage Refactor

## Summary

This refactor moves durable agent storage and access out of the implicit
`OrchestratorEngine.agents` cache and into an explicit file-backed store on the
orchestrator side.

The `vibrant/agents/*` runtime and protocol code is intentionally unchanged.

## What Changed

- Added `AgentRecordStore` in `vibrant/orchestrator/services/agent_records.py`
- Refactored `AgentRegistry` to read/write agent records through that store
- Updated `StateStore` to coordinate Gatekeeper result persistence with the
  agent store instead of always routing through `OrchestratorEngine`
- Updated `QuestionService.answer()` to use orchestrator services instead of
  calling `engine.answer_pending_question()` directly
- Updated lifecycle/facade/TUI compatibility reads to prefer orchestrator
  services over the engine's in-memory `agents` dict

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

- `vibrant/orchestrator/services/agent_records.py`
- `vibrant/orchestrator/services/agents.py`
- `vibrant/orchestrator/services/state_store.py`
- `vibrant/orchestrator/services/questions.py`
- `vibrant/orchestrator/lifecycle.py`
- `vibrant/orchestrator/services/__init__.py`
- `vibrant/orchestrator/facade.py`
- `vibrant/tui/app.py`

## Follow-up Work

- Split question storage into its own durable store instead of keeping it in
  `state.json`
- Move more derived workflow projections out of `state.json`
- Replace remaining engine-based compatibility paths with service/query reads
- Shrink `OrchestratorEngine` to workflow-state persistence and migration only
