# MCP Schema Degradation Mitigation Plan

## Problem summary

When Codex consumes MCP tool schemas, object-array parameters that rely on local JSON Schema references (`$defs` + `$ref`) can be degraded in the model-visible tool definition. A common failure mode is arrays of object references being shown as `string[]`, while server-side runtime validation still correctly expects structured objects.

For tools like Snowflake `query_semantic_view`, that yields a predictable pattern:

- Model first emits string entries (for example `"PAID.PAID_DT"`).
- MCP runtime rejects inputs because the contract expects objects (for example `{ "table": "PAID", "name": "PAID_DT" }`).
- The same call may only succeed on a second attempt after the model infers shape from the error.

## Upstream Codex finding (codex-rs)

A direct review of `codex-rs/core/src/tools/spec.rs` shows MCP schemas are passed through `sanitize_json_schema()` during tool conversion.

That sanitizer enforces/infers `type` on every schema object and defaults unknown forms to `"string"`. For nodes that are primarily local refs (for example `{"$ref": "#/$defs/SemanticExpression"}`), this can introduce a synthetic `"type": "string"` instead of preserving the referenced object shape.

In practice, this is consistent with the observed degradation where object-array inputs can appear as string arrays in model-visible tool metadata, despite MCP runtime still validating against the original object contract.

## Constraints in Vibrant's current architecture

Vibrant currently launches Codex as a subprocess via `codex app-server` and communicates over JSON-RPC through `CodexClient` and `CodexProviderAdapter`.

Because tool registration and schema adaptation happen inside Codex, Vibrant cannot directly patch the model-visible schema emitted by Codex for external MCP servers.

That means mitigation should happen in one (or more) of these places:

1. **Upstream Codex fix** (best long-term): inline/dereference local refs before tool registration.
2. **Server-side schema shaping** (practical now): publish tool schemas that avoid local refs.
3. **Vibrant preflight + guidance + retry behaviors** (defensive fallback in our layer).

## Recommended mitigation stack

### 1) Immediate: server-side schema flattening for affected MCP tools

For any MCP server we own (or can wrap), publish an LLM-friendly `inputSchema` that is already inlined and avoids local refs for hot-path tools. Keep runtime validation strict.

- Preserve canonical model classes internally.
- Export a flattened schema specifically for tool metadata.
- Prefer explicit object schemas in `items` over `$ref` indirection for object arrays.

This is the fastest way to prevent first-call failures without waiting on Codex release cycles.

### 2) Immediate: Vibrant-side tool-shape hint injection

At session start, fetch tool schemas from MCP (`tools/list`) and detect risky patterns:

- array `items` containing local `$ref`
- nested `$defs` used by parameters likely to be object arrays

Then inject a compact, deterministic call-shape hint into the agent instruction context, for example:

- `query_semantic_view.dimensions[] expects objects: {table: string, name: string}`
- `query_semantic_view.metrics[] expects objects: {table: string, name: string}`

This does not alter Codex tool registration but materially improves first-try call quality.

### 3) Immediate: deterministic repair/retry on validation failures

Add a targeted retry policy around MCP validation failures that indicate object-vs-string mismatches.

Suggested flow:

1. Detect validation errors matching `"Input should be a valid dictionary"` (or equivalent typed-object mismatch).
2. If original arguments use dotted-string forms like `TABLE.COLUMN`, transform to object form using known field mapping.
3. Retry once with repaired payload.
4. Emit a structured diagnostic event so runs remain auditable.

This is especially useful for common semantic-expression formats and avoids user-facing friction.

### 4) Short term: MCP proxy normalizer (optional)

Introduce a lightweight MCP proxy in front of external servers used by Vibrant runs:

- On `tools/list`, dereference/inline local refs in `inputSchema` before returning to Codex.
- On `tools/call`, forward requests unchanged (or optionally perform safe coercions with explicit logging).

This isolates the workaround from provider internals and can be enabled per-server.

### 5) Long term: remove workaround when upstream fix is available

Track Codex issue resolution and gate mitigations behind a feature flag:

- `mcp_schema_ref_workaround = on|off|auto`

In `auto`, keep mitigation active only for known-bad Codex versions.

## Prioritization

1. **Do now:** server-side flattening where we control schema + Vibrant instruction hints.
2. **Do next:** deterministic repair/retry for known object-array mismatches.
3. **Optional:** MCP proxy normalizer if many third-party servers are affected.
4. **Eventually:** retire mitigations once Codex natively preserves `$defs/$ref` object-array shapes.

## Operational safeguards

- Log both original and repaired payloads (redacted as needed) with a stable operation id.
- Make retries single-shot to avoid loops.
- Keep fallback transformations schema-aware and explicit; do not apply broad coercion.
- Surface a concise warning in agent output when repair was required.

## Why this fits Vibrant now

This plan aligns with Vibrant's current layering:

- Provider bridge stays thin and compatible with upstream Codex.
- Orchestrator/agent layers can add guidance and retry logic without forking Codex behavior.
- MCP-specific normalization can live as an external/proxy concern, consistent with transport boundary design.
