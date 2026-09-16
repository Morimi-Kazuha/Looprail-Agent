# PICO Medium+ Reconstruction
## Luna MAX Execution Report — Phase 2

**Report date:** 2026-09-15  
**Repository:** `D:\Agent Learning\Pico Agent`  
**Working-tree HEAD:** `0ae70289b282bdc808e668bd267c7370ffd0e5b8`  
**Approved demo surface:** `pico run`  
**Authoritative baseline:** `.\scripts\run_medium_baseline.ps1`

## 1. Task

Phase 2 hardened and documented the existing Tool execution boundary as a
Runtime-owned contract. The shipping `ToolRegistry` was retained as the single
execution authority; no ToolRuntimeV2, second registry, replay engine, or Agent
architecture rewrite was introduced.

The mandatory pre-implementation audit was completed before source changes. It
covered the Provider ToolCall to AgentLoop to Registry to Tool/schema to
execution to EffectJournal to Observation path, plus filesystem, shell,
tracing, and directly relevant tests.

## 2. Status

**PASS — READY FOR WEB REVIEW**

The Phase 2 contract tests, targeted Tool Runtime regression group, and the
authoritative Medium+ baseline pass. One real gap was found and fixed: the
shipping `ExecTool` inherited the base `UNKNOWN` capability even though it is a
process-execution tool. It now explicitly declares the existing
`ToolEffect.EXECUTE`; no public schema or result format changed.

There are five known failures in the separately run full `tests/test_sandbox_unit.py`
collection. They are existing Windows/Unix platform assumptions outside the
authoritative baseline and outside the Phase 2 diff: Unix `sleep` and `$VAR`
shell syntax, a Unix `/data` path fixture, and a slash-normalization assertion.
The cross-platform `TestExecToolWithMockExecutor` shell subset and all supported
baseline tests pass; these five observations are recorded rather than hidden.

After this report, Phase 2 stops and waits for Web Review. No Recovery,
Context, Memory, Eval, CLI Hardening, or Evolver work was started.

## 3. Scope and Compatibility

### Included

- Existing `ToolRegistry` resolution, validation, timeout, cancellation,
  normalization, effect, and observation boundaries.
- Narrow shell effect classification fix for `ExecTool`.
- Filesystem/path and shell failure-boundary tests.
- Effect lifecycle and uncertain-effect tests.
- Runtime contract documentation and deliberate baseline-manifest inclusion.

### Excluded

- Recovery orchestration or cross-process replay.
- Checkpoint, Context, Memory, Eval, PicoBench, CLI visual, TUI, channels,
  cron, AppWorld, Evolver, distributed execution, or a new permission platform.
- VM sandbox construction or a claim that `DirectExecutor` is isolated.
- Broad schema/error/result-format changes.

Compatibility was preserved. The only production change is additive capability
metadata in `pico/agent/tools/shell.py`; existing Tool schemas, text results,
`ToolResult.failed`, timeout messages, and public error conventions remain
unchanged.

## 4. Mandatory Pre-Implementation Audit

| Boundary | Observed shipping behavior |
|---|---|
| Provider ToolCall | `LLMResponse.tool_calls` supplies stable name, id, and arguments. |
| AgentLoop | Builds `ToolInvocation` with call/session/iteration/origin/turn context and delegates the batch to `self.tools.execute_many(...)`. |
| Resolution | `ToolRegistry` is the model-name to Tool instance directory; unknown names return a failed normalized result and do not create an effect. |
| Validation | Registry applies `Tool.cast_params` then `Tool.validate_params`; required/type/range/structured errors stop before tool code. Existing intentional coercion/extra-field behavior is retained. |
| Runtime execution | Registry calls `execute_with_context` inside `asyncio.wait_for` using the Tool timeout or the 300-second fallback; blocking interaction tools are intentionally exempt. |
| Error/result boundary | `ToolResult` is a `str` subclass with an explicit `failed` flag. Exceptions, timeouts, `Error...` strings, and ToolResults are normalized without exposing raw Python stack traces. |
| Effect classification | Existing `ToolEffect` maps to `EffectClass`; filesystem reads/writes and web calls declare capabilities. `ExecTool` was the concrete omission and was fixed to `EXECUTE`. |
| Effect persistence | With a journal, Registry durably appends `PREPARED` and `RUNNING` before Tool code, then commits known outcomes or records conservative `FAILED`/`UNKNOWN`. |
| Cancellation | A started cancellation appends `UNKNOWN` when possible and re-raises `CancelledError`; it is not converted into a successful model observation. |
| Filesystem boundary | `_resolve_path` expands/normalizes paths and validates the resolved path against the configured allowed directory, including traversal and symlink resolution. |
| Shell boundary | `ExecTool` applies the configured best-effort deny/allow/workspace guards, delegates to `SandboxExecutor`, bounds execution/output, and preserves stdout/stderr/exit code. |
| Trace/audit | `ToolRegistry.execute` is instrumented as `tool.call`; semconv records call, turn, effect, failure, duration/result-preview, and trace context. Effect JSONL records lifecycle and normalized error class. |

## 5. Tool Runtime Map

The actual supported path after Phase 2 is:

```text
Provider LLMResponse.tool_calls
        |
        v
AgentLoop: ToolInvocation(name, arguments, call/session/turn context)
        |
        v
ToolRegistry.execute_many
        |  resolve_invocation / observation callbacks
        v
ToolRegistry.execute_invocation -> execute
        |
        +--> resolve registered Tool
        +--> cast and validate arguments
        +--> classify ToolCapability/effect
        +--> EffectJournal PREPARED -> RUNNING (when enabled)
        +--> execute_with_context inside timeout boundary
        +--> normalize ToolResult/error
        +--> finalize effect state
        +--> return ToolExecution and ToolEvent observation
        v
AgentLoop adds the Tool result to model context and continues or finalizes
```

Progressive-disclosure `tool_call` also uses this same authority: its
`ToolSearchController.call` creates a child invocation and calls
`registry.execute_invocation`. It is not a second executor.

## 6. Authority Boundary

| Owner | Contract responsibility |
|---|---|
| Model | Proposes the tool name, arguments, and next task action. It does not authorize or execute host effects. |
| AgentLoop | Owns the provider/tool-call loop, context/session messages, iteration limits, and ToolEvent/observation delivery. It delegates requested execution to the Registry. |
| Tool Runtime / `ToolRegistry` | Owns lookup, argument casting/validation, effect classification, journal gate, timeout, cancellation semantics, normalized failure/result boundary, terminal effect state, trace instrumentation, and `ToolExecution` evidence. |
| Individual Tool | Owns its schema, business operation, declared capability, local path/command/external guards, and Tool-specific result content. It does not bypass the Registry in the approved mainline. |

The Runtime policy boundary is intentionally small: capability/effect metadata,
read-only concurrency gating, journal preconditions, timeout, and Tool-owned
filesystem/shell guards. There is no RBAC/ACL/enterprise policy service.

## 7. Tool Contract Matrix

| Concern | Existing Before Phase 2 | Change | Final Contract | Evidence |
|---|---|---|---|---|
| Resolution | Registry lookup with deterministic unknown-tool failure | No change | Unknown names fail with `ToolResult.failed=True`; no substitution and no effect record | `test_unknown_tool_fails_before_effect_creation`; `registry.py` |
| Validation | Schema-driven cast plus recursive validation | No change | Missing, wrong, or malformed object arguments fail before Tool execution; intentional coercion remains | `test_invalid_arguments_are_rejected_before_tool_execution`; existing Registry validation tests |
| Policy | Capability classification plus Tool-owned guards; no large policy engine | Add only `ExecTool`'s missing capability declaration | Runtime distinguishes READ, LOCAL_WRITE, EXECUTE, EXTERNAL, and conservative UNKNOWN without inventing ACLs | `test_shell_nonzero_exit_is_failed_execute_effect`; filesystem/shell guard tests |
| Timeout | Per-Tool timeout or 300-second Registry fallback; blocking interaction exemption | No change | Hangs are bounded where the Tool contract permits; timeout returns normalized failure and terminal evidence | `test_timeout_returns_failure_and_finalizes_read_effect`; existing timeout tests |
| Cancellation | Task cancellation propagates; running effect becomes UNKNOWN where journaled | No change | No atomic-cancel claim; post-start uncertainty remains visible and is not replayed | `test_cancellation_during_external_effect_is_recorded_as_unknown`; existing cancellation tests |
| Errors | Failed flag plus compatible `Error:` text and caught exception class in effect record | No public taxonomy churn | AgentLoop receives a stable failed/success signal; journal distinguishes caught `TimeoutError`, exception classes, and uncertain effect state | exception/timeout/escape/shell tests; `semconv.tool_call` |
| Result | `ToolResult(str)` boundary; Tool-specific strings and `ExecResult.as_text` | No change | Plain, structured, empty, and non-serializable return values become predictable string ToolResults; `failed` is explicit | `test_registry_result_boundary_is_toolresult_string`; existing output-limit tests |
| Effects | EffectJournal with PREPARED/RUNNING/terminal states and conservative UNKNOWN | No architecture change; shell class fix | Tool code runs only after prepared/running facts; terminal state reflects known read/write outcome or uncertainty | successful mutation/read, cancellation, and history tests |
| Trace | `tool.call` instrumentation and semconv fields already present | No change | Tool/call/turn/effect/failure/timing/result-preview evidence is locally auditable | `test_tracing_api`, `test_no_otel_tracing`, `semconv.py` |
| Filesystem | `_resolve_path`, allowed-dir checks, read/write capabilities, path/security tests | No change | Configured workspace confinement rejects traversal, absolute escape, and resolved symlink escape | `test_filesystem_escape_is_rejected_and_never_committed`; existing path/security tests |
| Shell | Injected `SandboxExecutor`, best-effort guards, timeout/output/exit-code handling; `ExecTool` effect was implicit UNKNOWN | Add `ToolCapability(effect=ToolEffect.EXECUTE)` | Bounded local process execution is journaled as EXECUTE; non-zero exit is failed and conservatively UNKNOWN; DirectExecutor is not full sandboxing | `test_shell_nonzero_exit_is_failed_execute_effect`; `TestExecToolWithMockExecutor`; `shell.py` |

## 8. Failure Matrix

| Input / Trigger | Runtime Decision | Observation / Error | Effect State | Test |
|---|---|---|---|---|
| Unknown name `missing_tool` | Stop at Registry resolution; do not call a Tool | `Error: Tool 'missing_tool' not found...`, `failed=True` | No effect record | `test_unknown_tool_fails_before_effect_creation` |
| Missing required field, wrong integer type, non-object/list, or `None` payload | Cast/validate before execution; reject malformed model output | `Error: Invalid parameters...`, `failed=True`; probe call count remains zero | No effect record | `test_invalid_arguments_are_rejected_before_tool_execution` |
| Valid `read_file` inside allowed workspace | Resolve, prepare/run READ effect, execute, normalize | Numbered file text as successful `ToolResult` | `PREPARED -> RUNNING -> COMMITTED`, class READ | `test_successful_read_has_committed_read_effect` |
| Valid `write_file` inside allowed workspace | Resolve, record hash pre/post evidence, execute, verify postcondition | Successful write result; file content is present | `PREPARED -> RUNNING -> COMMITTED`, class LOCAL_WRITE | `test_successful_mutation_has_committed_local_write_effect` |
| Tool raises `RuntimeError` | Catch at Registry boundary; preserve failure signal | `Error executing exception_probe...`, `failed=True` | READ `FAILED`; `error_class=RuntimeError` | `test_tool_exception_is_normalized_and_read_effect_fails` |
| Read probe never returns before its configured timeout | `asyncio.wait_for` cancels the bounded call and normalizes timeout | `Error: Tool 'wait_probe' timed out...`, `failed=True` | READ `FAILED`; `error_class=TimeoutError` | `test_timeout_returns_failure_and_finalizes_read_effect` |
| External probe is cancelled after it reaches execution | Persist uncertainty when possible and re-raise task cancellation | Caller receives `CancelledError`; no false success result | EXTERNAL `UNKNOWN`; `error_class=CancelledError` | `test_cancellation_during_external_effect_is_recorded_as_unknown` |
| `read_file` path `../outside.txt` or `C:/outside.txt` | Resolved-path allowed-dir guard rejects before file access | Normalized `Error` containing outside-path failure | READ `FAILED` | `test_filesystem_escape_is_rejected_and_never_committed` |
| Shell executor returns exit code 7 | Shell preserves stdout/stderr/exit receipt and marks ToolResult failed | Failed result includes `STDERR:` and `Exit code: 7` | EXECUTE `UNKNOWN` because failed opaque process effects are not proven absent | `test_shell_nonzero_exit_is_failed_execute_effect` |
| Journaled successful terminal call | Append immutable lifecycle snapshots and validate transition | `ToolResult` carries the same effect id | `PREPARED -> RUNNING -> COMMITTED` | `test_successful_terminal_effect_has_prepared_running_committed_history` |
| Effect may have occurred but completion is not provable | Do not convert uncertainty to a retryable/failed fact | Failed/propagated observation plus durable UNKNOWN where possible | `UNKNOWN`; no automatic replay | cancellation test; existing opaque/external effect tests |

## 9. Effect State Matrix

| State | Actual entry condition in the shipping Registry | Terminal/continuation meaning | Evidence |
|---|---|---|---|
| `PREPARED` | Arguments passed validation and the first journal snapshot is appended | Durable intent/inputs exist; Tool code has not run | Existing crash-hook test; successful lifecycle test |
| `RUNNING` | Immediately before `execute_with_context`, after the prepared snapshot | Execution may have started; a crash can leave this fact for later inspection | Existing running-crash test; lifecycle test |
| `COMMITTED` | Successful READ; verified local-write postcondition; or successful opaque execution return | Runtime observed a successful call boundary; does not mean the user task is complete | Read, mutation, and successful terminal tests |
| `FAILED` | Known READ failure/timeout, or a local-write failure whose precondition still matches; also explicit precondition conflict | Failure is known without claiming an opaque side effect | Exception, timeout, path, and existing local-write tests |
| `UNKNOWN` | Started cancellation, failed opaque EXECUTE/EXTERNAL call, or unverifiable local-write postcondition | Outcome/effect occurrence is uncertain; this Phase does not replay it | Cancellation and shell non-zero tests; existing effects tests |

The Registry's normal execution path establishes `PREPARED` and `RUNNING`
before Tool code. A terminal journal append failure intentionally does not
fabricate a terminal state; the durable last fact remains the last successfully
written snapshot and the returned result reports the outcome as unknown.
This Phase does not implement cross-process recovery or replay.

## 10. Mainline Bypass Audit

No approved `pico run` mainline bypass was found.

- `pico/agent/loop/main.py` creates `ToolInvocation` values for provider
  ToolCalls and has one model-requested execution delegation at
  `self.tools.execute_many(...)`.
- Registry callbacks produce the start/complete ToolEvent observations; the
  returned `ToolExecution` is then added to AgentLoop context.
- Progressive-disclosure `tool_call` resolves target names and calls the same
  `ToolRegistry.execute_invocation`; it does not call a second executor.
- The audit found no direct model-requested `tool.execute(...)` call in the
  approved mainline. Legacy/deferred surfaces were not broadened or rewritten.

## 11. Shell and Filesystem Boundary Notes

`ExecTool` means bounded local process execution through the configured
`SandboxExecutor`. `DirectExecutor.is_sandboxed` is false and executes on the
host. Its deny-list, allow-list, workspace cwd/path checks, timeout, and output
limits are best-effort runtime guards; they are not a complete shell parser,
VM, OS sandbox, or proof of absence of side effects. BoxLite/real VM behavior
was not exercised in this Phase.

Filesystem tools use resolved paths and allowed-directory checks. Valid reads
and writes in the configured workspace were exercised with a temporary
workspace; traversal and absolute escape attempts were rejected. No access
boundary was broadened to accommodate platform-specific tests.

## 12. Trace and Durable Audit Evidence

The existing `tool.call` instrumentation and `EffectJournal` are sufficient for
the Phase 2 local audit contract. Together they preserve:

- tool name and model call id;
- session, turn, and current trace context;
- start/terminal timestamps and duration/result preview;
- effect id/class and lifecycle status;
- normalized failure signal and caught error class where available;
- hash-only local-write pre/post evidence for the supported `write_file` path.

No external observability service or distributed audit pipeline was added.

## 13. Test Evidence

### Phase 2 focused contract tests

```text
python -m pytest -q tests/test_phase2_tool_runtime_contract.py
19 passed in 1.35s
```

The test file covers resolution, malformed arguments, successful read and
mutation, exception, timeout, cancellation/UNKNOWN, filesystem escape, shell
non-zero exit/effect classification, result normalization, and successful
effect history.

### Targeted Tool Runtime regression

The Phase 2 test plus Registry, timeout, effect journal, supported shell-tool,
filesystem/path, search, security, and AgentLoop tool-observation tests passed:

```text
128 passed in 31.46s
```

The cross-platform shell-tool subset was
`tests/test_sandbox_unit.py::TestExecToolWithMockExecutor`; it passed as part
of this group.

### Full sandbox-unit observation

The separately run full `tests/test_sandbox_unit.py`-containing group produced
`201 passed, 5 failed`. The five failures are not in Phase 2 production code
and are not in the accepted explicit baseline:

- `TestSandboxConfigValidators.test_extra_volumes_valid`: Unix `/data` fixture
  rejected by Windows absolute-path validation.
- `TestDirectExecutor.test_exec_timeout`: Unix `sleep` is not a Windows `cmd`
  command.
- `TestDirectExecutor.test_exec_env` and
  `TestDirectExecutor.test_host_env_not_inherited`: Unix `$VAR` syntax is not
  expanded by Windows `cmd`.
- `TestBoxliteTranslateCwd.test_subdir_translates_correctly`: assertion expects
  POSIX slashes for a Windows path.

These failures are reported as existing platform-test assumptions, not marked
as Phase 2 proof or silently folded into the baseline.

### Static check

```text
ruff check pico/agent/tools/shell.py tests/test_phase2_tool_runtime_contract.py
All checks passed!
```

## 14. Baseline Preservation

The Phase 2 core test was deliberately added to the explicit manifest in
`scripts/run_medium_baseline.ps1`. The manifest remains an explicit supported
test list; full-repository collection was not substituted.

Final authoritative run:

```powershell
.\scripts\run_medium_baseline.ps1
```

Actual result:

```text
584 passed in 71.68s (0:01:11)
```

This is the Phase 1 accepted `565 passed` plus the 19 new Phase 2 contract
tests, with no supported-baseline regression.

## 15. Files Changed

### Production source

- `pico/agent/tools/shell.py` — imported existing `ToolCapability`/
  `ToolEffect` and declared `ExecTool` as `EXECUTE`.

### Tests

- `tests/test_phase2_tool_runtime_contract.py` — 19 focused Runtime contract
  and failure-boundary tests.

### Docs

- `docs/tool-runtime-contract.md` — supported authority/effect/result/shell/
  filesystem contract.
- `docs/medium-plus-baseline.md` — records deliberate Phase 2 manifest
  inclusion.

### Baseline manifest

- `scripts/run_medium_baseline.ps1` — added the Phase 2 contract test file to
  the explicit manifest.

### Report

- `reports/PHASE_02_LUNA_EXECUTION_REPORT.md` — this report.

Phase 0/Phase 1 artifacts and the pre-existing recovery draft in the working
tree were preserved and not expanded in this Phase. The existing Phase 1
`pyproject.toml` marker change was not modified by Phase 2.

## 16. Claims Proven

- `ToolRegistry` remains the single approved model-requested Tool execution
  authority.
- Unknown names and invalid arguments fail before Tool code and before effect
  creation.
- Registry timeout, exception normalization, explicit Tool failure, and
  cancellation behavior are tested at the execution boundary.
- READ, local-write, EXECUTE, EXTERNAL, and conservative UNKNOWN effect
  semantics are represented by the existing capability/journal model; shell
  now declares EXECUTE explicitly.
- Successful workspace read/mutation and representative escape attempts obey
  the existing filesystem boundary.
- Shell non-zero exit preserves a failed result and uncertain EXECUTE effect
  evidence.
- Effect lifecycle evidence retains the same effect id through
  `PREPARED/RUNNING/terminal` snapshots.
- AgentLoop receives normalized Tool observations through Registry callbacks
  and returned `ToolExecution` values.
- The supported Windows/Python 3.12 explicit baseline passes with 584 tests.

## 17. Claims NOT Proven

This Phase does not prove:

- full sandboxing;
- cross-process recovery;
- safe automatic replay of `UNKNOWN`;
- distributed execution safety;
- an enterprise permission model;
- complete external-side-effect atomicity;
- exactly-once execution;
- complete shell-language/path parsing for every platform command form;
- real BoxLite/VM isolation or live external-provider execution.

## 18. Stop Condition

Phase 2 is complete for Web Review. No next-phase implementation was started.

**PASS — READY FOR WEB REVIEW**

