# TUI Todo List

> Tasks marked with `[ ]` are pending, while those marked with `[x]` are completed. Deferred tasks should not be implemented until unmarked.

## Service Migration

[x] Hook TUI events into real API calls, as is detailed in the [Orchestrator API documentation](../vibrant/orchestrator/STABLE_API.md).
[x] Remove remaining legacy multi-thread/session actions in `vibrant/tui/app.py`.
[ ] (Defer) After reloading the application the session needs to be reloaded from the file system.

## Widget Improvements

### Agent Output

[x] Review the current `AgentOutput` panel against the redesign and keep only the log detail that belongs in the `Agent Logs` tab.
[ ] Ensure all interactions are done with facade methods with the Orchestrator, see [Orchestrator API documentation](../vibrant/orchestrator/STABLE_API.md) for details on the stable API contract. Direct access to engine internals are forbidden and must be substituted. This includes:
    [ ] Differentiating between chat messages and agent thoughts. Display agent thoughts as a collapsible section with a spinner while going, see [textual blog](https://textual.textualize.io/blog/2022/11/24/spinners-and-progress-bars-in-textual/) for implementation.
    [ ] Ensure that the chat history is loaded each time the application is started, and the messages are rendered in the `Chat History` tab.
[ ] The second chat request gets no output in the screen. This needs to be fixed.

### Input Box

[ ] Implement `Ctrl + Backspace` to delete the last word in the input box.
[ ] Implement autocompletion for commands (starting with `/`) and file paths (starting with `@`).
[ ] Implement a command history that can be navigated with the `Up` and `Down` arrow keys when the input box is focused. Query the history from the Orchestrator.

### Task Status

[ ] Implement stub in `vibrant/tui/widgets/task_status.py`.

## Screen Fixes

### Initialization modal (select directory)

[ ] (Defer) When the dir input is not focused, pressing `Enter` should confirm the choice.

### Consensus Modal

[x] Change the modal into a panel that is docked in these places:
    - Planning: As a toggle-able side panel on the left side of the screen.
    - Vibing: As one of the four tabs in the main screen.
[x] Lookdev: Change it into a markdown viewer / writer so that the gatekeeper can update the consensus and the user can see the changes in real time, and edit if needed. (The metadata should not be editable). Wrap this into a component.

### Planning Screen

[ ] The second response from the agent does not render.

### Vibing Screen

[x] Use Tabs instead of buttons to select tab to display.
[ ] Replace the `TaskStatusView` stub with a real task-status panel that shows the selected task, progress, and current execution details.
[ ] Replace the temporary roadmap-loading notices with a proper loading spinner for the `Task Status` and `Chat History` tabs before roadmap generation finishes.
[ ] Implement Roadmap-loading placeholder.
