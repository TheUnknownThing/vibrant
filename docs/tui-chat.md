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