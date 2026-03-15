# TUI Reference

![tui](images/tui-overview.png)

This document is the source of truth for the Vibrant terminal UI.

On all screens the footer is present.

## Initialization Screen

Upon entering the software, Vibrant checks whether the present directory has
already been initialized. If so, it enters either the planning screen or the
vibing screen depending on workflow state. Otherwise, the initialization screen
is shown.

The initialization screen has a logo at the top and three options:

- Initialize Project Here
- Initialize Project (Select Directory)
- Exit

When choosing a directory, the path input should support filesystem autocomplete with a dropdown list so users can quickly select an existing folder.

## Planning Screen

The planning phase is also named "Consensus Building". "Tell me what you want
to build" is displayed as the default text in the user input box, and the user
engages in a conversation with the Gatekeeper. The Gatekeeper asks questions
until it has enough information to build a consensus, not a detailed roadmap.
When planning is complete, the Gatekeeper ends the planning phase through the
orchestrator and the UI switches into the vibing phase automatically.

Users can use `f7` to toggle the consensus view, which is a side panel
(default hidden) that shows the current consensus in markdown. The panel
automatically pops up when the Gatekeeper first writes to the consensus.

The planning screen is depicted in the left part of the image above.

## Vibing Screen

The vibing phase is where the Gatekeeper uses the orchestrator through MCP to
execute tasks, and the user can inspect project progress plus the current
consensus.

The screen, shown in the right part of the image above, is divided into these sections:
- Appbar (top): As always, shows project name (pwd dir name) after Vibrant version.
- Task Bar (left): Shows queued, running, and blocked tasks. Selecting a task updates the Task Status tab.
- Main Screen (right-top): Shows one of four tabs:
  - Task Status: shows the progress of the current task, and is the default tab when entering the vibing phase.
  - Chat History: shows the Gatekeeper conversation, and is the default tab when entering from the planning phase.
  - Consensus: shows the consensus document.
  - Agent Logs: shows provider and canonical debug logs.

Before the roadmap is generated, both the Task Status and the Chat History
tabs show a "Generating Roadmap" loading spinner.

## Chat History Tab

The chat history renders processed orchestrator conversation frames rather than
raw provider logs.

Each message block has:

- a role: user, assistant, or system
- markdown text parts for normal message content
- reasoning summary parts rendered in a collapsible section, with a spinner while the summary is still streaming
- tool-call parts that show the tool name plus `executing`, `success`, or `failed` status

## Input Box

The shared input box supports:

- `Ctrl+Backspace` to delete the previous word
- slash-command autocomplete for commands such as `/logs`
- `@path` autocomplete rooted at the project directory
- message history navigation with the Up and Down arrow keys while the input is focused

## Keyboard Shortcuts

- `f1`: Help Screen (Planning, Vibing)
- `f2`: Pause workflow (Vibing)
- `f5`: Toggle Task (Vibing, switch to one of the four tabs in the main screen)
- `f6`: Toggle Chat History (In Vibing, switch to one of the four tabs in the main screen)
- `f7`: Toggle Consensus (In Planning, side panel; In Vibing, switch to one of the four tabs in the main screen)
- `f8`: Toggle Agent Logs (In Vibing, switch to one of the four tabs in the main screen)
