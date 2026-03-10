# TUI Todo List

## Remaining work after the screen cleanup

### Planning screen
- Refine the planning chat layout and spacing to match the redesign mockup more closely.
- Decide whether non-gatekeeper Codex threads still belong in the TUI; if yes, redesign them to fit the new flow instead of restoring the old thread-list UI.
- Add a clearer planning-phase status indicator around `/vibe` so the transition feels explicit.

### Vibing screen
- Replace the `TaskStatusView` stub with a real task-status panel that shows the selected task, progress, and current execution details.
- Replace the temporary roadmap-loading notices with a proper loading spinner for the `Task Status` and `Chat History` tabs before roadmap generation finishes.
- Make the task bar fully match the plan: distinguish running, queued, blocked, and completed tasks, and support selecting a task to update the right-hand panel.
- Decide how the right-hand tabs should sync with task selection, especially the default tab rules when entering vibing from planning vs reopening an existing project.

### Consensus and logs
- Review the current `ConsensusView` tab against the redesign and simplify it if a full-file view is preferred over the current summary-heavy widget.
- Review the current `AgentOutput` panel against the redesign and keep only the log detail that belongs in the `Agent Logs` tab.

### App flow
- Audit the remaining legacy multi-thread/session actions in `vibrant/tui/app.py` and either remove them or redesign them to fit the new TUI plan.
- Add focused tests for the new screen-host flow: initialization modal, planning screen, `/vibe` transition, and vibing tab defaults.
- Remove any additional unused widgets or helpers that were only supporting the old four-panel layout once the new behavior is settled.

## Intentionally left as stubs for now
- `vibrant/tui/widgets/task_status.py`
- Roadmap-loading placeholder inside `vibrant/tui/screens/vibing.py`
