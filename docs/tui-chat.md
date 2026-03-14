# Chat Panel Design

The chat panel consists of multiple message blocks, each representing a block of conversation.

- MessageBlock
    - Role: User | Assistant (or System/Model)
    - Parts: `List[MessagePart]`

- MessagePart
    - TextPart:
        - text: `String` (rendered as markdown)
    
    - ReasoningPart
        - status: InProgress | Completed
        - content: `TextPart` (the internal thought text, hidden within a collapsable section)
    
    - ToolCallPart
        - toolName: `String`
        - status: Executing | Success | Failed

---

Implementation Method:

## Step 1: Rewrite Chat Panel

- Update the chat panel to use the new facade API as defined in `vibrant.orchestrator.facade` (read the code for source of truth).
- The conversation renderer has been cleared. Implement a minimal version for testing purposes.
- DO NOT write any test, but ensure that the program does run without erroring out directly. You may use loggers to add debug statements.

## Step 2: Implement Conversation Renderer

- Implement the conversation renderer to render the conversation based on the message block structure defined above.
- Ensure that the renderer can handle the different types of message parts and render them appropriately.
- DO NOT write any test.

---

## Mock Provider For TUI Development

To exercise the chat panel without waiting on a real Codex session, enable the built-in mock provider in `.vibrant/vibrant.toml`:

```toml
[provider]
mock-responses = true
```

The mock adapter emits the same canonical event types the TUI already consumes, including streamed `reasoning.summary.delta`, `content.delta`, `request.opened`, `request.resolved`, `runtime.error`, and `turn.completed`.

Use message markers to force specific UI states while testing:

- `[mock:tool]` streams a tool-call lifecycle before the final answer.
- `[mock:question]` pauses for user input and resumes after you answer.
- `[mock:error]` emits a runtime error instead of completing normally.
- `[mock:long]` emits a longer streamed response for scroll and persistence checks.
