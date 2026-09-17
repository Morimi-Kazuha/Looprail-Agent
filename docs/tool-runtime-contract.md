# Tool Runtime Contract

`looprail/agent/tools/registry.py` is the model-tool execution boundary used by
the supported `looprail run` path. This page describes the existing contract;
it does not add a second runtime or executor.

```text
Provider ToolCall
  → AgentLoop builds ToolInvocation
  → ToolRegistry resolves and validates
  → ToolRegistry classifies the declared effect
  → EffectJournal records PREPARED/RUNNING when enabled
  → ToolRegistry executes with the tool/registry timeout
  → ToolRegistry normalizes the result or exception
  → EffectJournal records the terminal state
  → AgentLoop receives the ToolExecution/ToolResult observation
```

## Authority

- The model proposes a tool name, arguments, and next action.
- `AgentLoop` owns the Provider/tool-call loop and delegates model-requested
  execution to the Registry.
- `ToolRegistry` owns lookup, schema-driven casting and validation, timeout,
  cancellation handling, result/error normalization, effect records, and the
  callback observation boundary.
- Individual tools own their declared schema, business behavior, and local
  guards. Filesystem tools resolve against their configured workspace; shell
  execution uses the injected `SandboxExecutor` boundary.

Unknown names and invalid arguments fail before tool code runs. Unknown names
do not create an effect record. Invalid arguments do not create an effect
record. Existing string-result compatibility is retained through `ToolResult`,
which is a `str` with a separate `failed` flag.

## Effect and failure semantics

Declared `READ`, `WRITE`, `EXECUTE`, and `EXTERNAL` capabilities map to the
journal's `READ`, `LOCAL_WRITE`, `EXECUTE`, and `EXTERNAL` classes. The shell
`ExecTool` explicitly declares `EXECUTE`; filesystem writes retain their
hash-based `LOCAL_WRITE` pre/postcondition evidence. Tools without a reliable
effect declaration remain `UNKNOWN` rather than being treated as reads.

With an effect journal, an executable call must durably pass through
`PREPARED` and `RUNNING` before tool code runs. A successful read or verified
local write commits. A known read failure is `FAILED`. A failed opaque,
external, or executable call, and a cancellation after the call started, are
`UNKNOWN`. `UNKNOWN` is not auto-replayed or silently rewritten into a safer
state by this contract.

The Registry returns compatible normalized text for timeout, exception, and
tool-declared error results. It does not expose raw Python tracebacks to the
model. Structured or otherwise non-string return values cross the current
string `ToolResult` boundary via `str(value)`; large-output truncation remains
owned by the individual tool, executor, or Context boundary.

## Shell and filesystem boundaries

`ExecTool` is bounded local process execution through the configured
`SandboxExecutor`. The default `DirectExecutor` is host execution with no VM
isolation; deny-list, allow-list, working-directory, and best-effort path
guards do not constitute a complete shell parser or sandbox. Non-zero exit
codes remain explicit failed results with stdout, stderr, and an exit-code
receipt.

Filesystem tools resolve paths and validate the resolved path against their
configured allowed directory. Traversal, absolute escape, and symlink escape
attempts are rejected where workspace confinement is configured.

Trace instrumentation and effect records retain tool name, call/session/turn
context, effect identity/class, arguments digest, lifecycle timestamps, and
normalized terminal failure information where applicable. This is local
auditability, not an external observability or distributed execution system.
