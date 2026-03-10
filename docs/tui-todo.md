# TUI Todo List

> Tasks marked with `[ ]` are pending, while those marked with `[x]` are completed. Deferred tasks should not be implemented until unmarked.

## Service Migration

[ ] Hook TUI events into real API calls, as is detailed in the [Orchestrator API documentation](../vibrant/orchestrator/STABLE_API.md).
[ ] Remove remaining legacy multi-thread/session actions in `vibrant/tui/app.py`.

## Widget Improvements

### Agent Output

[ ] Display agent thoughts as a collapsible section with a spinner while going, see [textual blog](https://textual.textualize.io/blog/2022/11/24/spinners-and-progress-bars-in-textual/) for implementation.

### Input Box

[ ] Implement `Ctrl + Backspace` to delete the last word in the input box.
[ ] Implement autocompletion for commands (starting with `/`) and file paths (starting with `@`).
[ ] (Defer) Implement a command history that can be navigated with the `Up` and `Down` arrow keys when the input box is focused.

### Task Status

[ ] Implement stub in `vibrant/tui/widgets/task_status.py`.

## Screen Improvements

### Initialization modal (select directory)

[ ] When the dir input is not focused, pressing `Enter` should confirm the choice.

### Vibing Screen

[ ] Use Tabs instead of buttons to select tab to display.
[ ] Replace the `TaskStatusView` stub with a real task-status panel that shows the selected task, progress, and current execution details.
[ ] Replace the temporary roadmap-loading notices with a proper loading spinner for the `Task Status` and `Chat History` tabs before roadmap generation finishes.
[ ] Implement Roadmap-loading placeholder.

### Consensus Modal

[ ] Rewrite the current `ConsensusView` tab so that it is rendered using the markdown tool.
[ ] Review the current `AgentOutput` panel against the redesign and keep only the log detail that belongs in the `Agent Logs` tab.
