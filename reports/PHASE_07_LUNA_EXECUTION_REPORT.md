# PICO Medium+
## Luna MAX Execution Report — Phase 7

**Report date:** 2026-09-16  
**Repository:** `<repo-root>`<br>
**Working-tree HEAD:** `0ae70289b282bdc808e668bd267c7370ffd0e5b8`  
**Approved product/demo surface:** `pico run`  
**Authoritative baseline command:** `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_medium_baseline.ps1`

## 1. Status

**PASS — READY FOR WEB REVIEW**

Phase 7 hardens the existing `pico run` path into a bounded, reviewer-facing
Runtime demonstration surface. It renders observations already produced by
Spine, AgentLoop, the Phase 2 Tool Runtime, ContextAssembler, Recovery and the
Phase 6 TraceStore. It does not introduce a second execution, state, recovery,
context, memory, effect or trace authority.

The final authoritative Medium+ manifest passed **653 tests in 84.20s**. The
frozen Phase 6 baseline was 643 tests; the deliberate increase is the explicit
addition of the 10-test Phase 7 CLI contract file to
`scripts/run_medium_baseline.ps1`. The Phase 7 contract file passed **10 tests
in 5.55s**, and the focused CLI/Spine/AgentLoop regression set passed **128
tests in 23.73s**.

Phase 6 remains accepted/frozen. Phase 7 is the only new phase executed in
this report. Phase 8, final acceptance and Evolver activation were not
started.

After this report: **STOP → WAIT FOR WEB REVIEW.**

## 2. Objective and Scope

The objective was to make the existing Medium+ Runtime legible in one
inspectable CLI experience:

~~~
Task
  -> Turn
  -> Context
  -> Provider decision
  -> Tool call / observation
  -> verification / continuation
  -> terminal Runtime state
  -> durable Run evidence
~~~

The implementation scope was limited to:

- `pico run` presentation and event observation;
- normal, verbose and quiet output levels;
- bounded Tool invocation/result rendering;
- terminal state and failure rendering;
- Session/Run/Turn identity and TraceStore discovery;
- existing Recovery/Resume state presentation;
- a deterministic local coding-task fixture and contract tests;
- the explicit Medium+ baseline manifest and this report.

No new Agent architecture, Tool Runtime, Recovery, Context, Memory, Trace,
PicoBench, Evolver, TUI, Web UI, channel, Cron or distributed-runtime
feature was added.

## 3. Mandatory Pre-Implementation Audit

The shipping path was inspected under `pico/cli/`, `pico/spine/`,
`pico/agent/`, `pico/tracing/` and `pico/session/` before the presentation
changes were made. The actual map is:

~~~
pico run
  -> pico/cli/commands.py registration
  -> pico/cli/agent_commands.py::agent
  -> load_runtime_config / resolve_foreground_paths
  -> pico/cli/_runtime_assembly.py::assemble_runtime
  -> Runtime.agent_loop
  -> pico/cli/_repl_spine.py::build_repl
  -> TurnRequest
  -> Scheduler.submit
  -> Origin lane / AgentTurnRunner
  -> AgentLoop.run_turn
  -> AgentLoop callbacks and Spine events
  -> DeliveryHub -> CliOutlet
  -> TurnEnded / TurnFailed lifecycle sink
  -> TraceStore.find_run_id(turn_id)
  -> durable per-Run JSONL evidence
  -> terminal CLI summary
~~~

The audit recorded the following existing behavior and Phase 7 seam:

| Boundary | Existing shipping owner | Phase 7 observation seam |
|---|---|---|
| Parser and command | Typer registration and `agent_commands.agent` | Add only `--verbosity`; preserve existing `pico run` flags and command name |
| Runtime assembly | `_runtime_assembly.assemble_runtime` | Pass the existing AgentLoop to the existing Spine wiring |
| Turn scheduling | `Scheduler`, lane and `AgentTurnRunner` | Observe `TurnEvent` lifecycle without changing scheduling |
| Tool execution | `ToolRegistry`, EffectJournal and Tool Runtime | Render actual `ToolEvent` START/COMPLETE events plus receipt fields |
| Context | `ContextAssembler` and Phase 4 metadata | Read bounded `context.assemble`/`context.compact` trace attributes |
| Memory | Phase 5 MemoryStore boundaries | Read bounded recall/write counts only; never dump the store |
| Recovery | `RecoveryProjector`, `RecoveryStateStore`, EffectJournal | Show durable state and fresh-Turn semantics; no automatic UNKNOWN replay |
| Trace | Phase 6 `TraceStore` | Resolve displayed Turn ID to Run ID and JSONL artifact |
| Terminal result | `TurnEnded`/`TurnFailed` and durable marker | Map Runtime facts to the human-facing status; never inspect final prose for success |
| Bare `pico` | Existing compatibility/deferred TUI surface | Remains outside Phase 7 scope |

The initial audit found that the CLI swallowed successful Tool events and did
not observe lifecycle terminal events. It also used one directory argument for
both Workspace Recovery state and the separately configured TraceStore. The
Phase 7 changes address those presentation seams without moving ownership.

## 4. Current CLI Map

The approved path is still:

~~~
pico run
  -> command parser
  -> existing config/provider/runtime assembly
  -> existing Scheduler / AgentTurnRunner
  -> existing AgentLoop and ToolRegistry
  -> existing Spine events and DeliveryHub
  -> CLI-only reporter and outlet
  -> existing TraceStore lookup
~~~

The supported command retains the existing public options:

~~~
pico run
  --message / -m
  --session / -s
  --continue / -c
  --resume / -r
  --workspace / -w
  --config
  --markdown / --no-markdown
  --logs / --no-logs
~~~

Phase 7 adds one small presentation option:

~~~
--verbosity normal|verbose|quiet
~~~

The `pico --version`, `pico --help` and existing `pico run` command remain
available. Bare `pico`/TUI was not migrated into this work.

## 5. Before vs After CLI Experience

### Before

The real runtime already executed the task, but the CLI path effectively
showed the final assistant text and generic Tool failures. Successful Tool
events were delivered through the hub and then swallowed by the old
`CliOutlet`; lifecycle completion was not retained by the CLI; and the final
screen did not prove which durable Trace contained the Run.

Bounded conceptual output was therefore close to:

~~~
Pico
  Fixed calculator.py and verified the focused test.
~~~

### After

The same runtime path now produces an inspectable bounded view:

~~~
PICO Run
Session  cli:<dynamic>
Run      pending
Task
  Find and fix the calculator bug, then run its test.
Turn     turn-<dynamic>
Agent
  ...
Tool
  grep return left - right calculator.py
Tool
  grep return left - right calculator.py
  OK committed (66 ms)
  effect: committed
  effect_id: effect-<dynamic>
  preview: calculator.py:4: return left - right
Tool
  read_file calculator.py
  OK committed (24 ms)
  effect: committed
Tool
  write_file calculator.py
  OK committed (29 ms)
  effect: committed
Tool
  exec python -m pytest -q test_calculator.py
  OK committed (810 ms)
  preview: . [100%] 1 passed ... Exit code: 0
Pico
  Fixed calculator.py and verified the focused test.
Run      trace-<dynamic>
Context
  estimated: 4403
  limit: 65536
  compacted: 0
  dropped: 0
Provider
  selected: fixture
  model: fixture/phase7-deterministic
Memory
  recalled: 0
  writes: 0
Result
  COMPLETED
Evidence
  run: trace-<dynamic>
  trace: ...\logs\runs\trace-<dynamic>.jsonl
  spans: 15
~~~

IDs and absolute temporary fixture paths are dynamic and are intentionally
bounded in this report. The contract test extracts the actual Run ID from the
CLI output and opens it with `TraceStore`; the transcript is not hardcoded.

## 6. CLI Authority Boundary

| Runtime authority | Owns | CLI may show |
|---|---|---|
| Scheduler / Spine lifecycle | Turn start, end, failure, cancellation and tool-failure counts | `Turn`, `Result`, cancellation/failure status |
| AgentLoop / ToolRegistry | Provider decisions, Tool invocation and Tool result semantics | Agent progress and actual Tool events |
| EffectJournal | Effect ID and `committed`/`failed`/`unknown` truth | Receipt status and bounded effect ID |
| ContextAssembler | Context composition, budget, compaction and overflow decision | Bounded counters/limit/estimate from durable trace |
| MemoryStore | Recall, scope, write eligibility and persistence | Bounded recalled/write counts only |
| RecoveryProjector / RecoveryStateStore | Durable inputs, checkpoint references and fresh-Turn projection | Recovery availability, previous Turn, new-Turn semantics and unknown warning |
| TraceStore | Durable bounded Run projection and lookup | Run ID, trace artifact reference and span count |
| CLI reporter/outlet | Human-readable formatting and output-level filtering | Nothing beyond presentation |

The CLI does not execute a Tool, commit an effect, choose Context, write
Memory, create Recovery state, seal a Trace, or certify workspace correctness.
It does not infer success from assistant text. `CliRunReporter` is an
observation layer; `CliOutlet` renders events already routed through Spine.

## 7. Event Rendering Matrix

| Runtime Event | Source | Normal Rendering | Verbose Rendering | Failure behavior |
|---|---|---|---|---|
| `Text` | `AgentLoop` -> `DeliveryHub` | Existing assistant response | Existing response; no hidden reasoning added | Text does not determine terminal status |
| `Notice(PROGRESS)` | `AgentLoop` callback | Bounded `Agent` progress when enabled | Same bounded progress | Notice is not a success signal |
| `Reasoning` | Existing AgentLoop callback | Bounded progress-compatible display when enabled | Same; no chain-of-thought dump | Not used for terminal inference |
| `ToolEvent START` | Tool Runtime callback -> AgentLoop -> Spine | `Tool` plus bounded name/target | Same | Start is not completion or success |
| `ToolEvent COMPLETE` | Tool Runtime callback -> AgentLoop -> Spine | Name, bounded result status and duration | Effect category, effect ID, bounded preview and duration | `FAIL <actual category>` or `? effect outcome unknown` |
| `TurnFailed` | Scheduler/lane lifecycle | `Failure` detail and final status | Same with bounded evidence/category | `FAILED`, `CANCELLED` or `BUDGET_EXCEEDED` from Runtime facts |
| `TurnEnded` | Scheduler/lane lifecycle | `Result COMPLETED` or tool-failure status | Same plus durable summaries | Tool failures remain `COMPLETED_WITH_TOOL_FAILURE` |
| `context.assemble` / `context.compact` | Phase 4 trace spans | Estimate, limit, compacted, dropped | Adds input budget | No context decision is made by CLI |
| `llm.call` | TraceStore durable record | Provider/model summary | Same identity fields | Lookup failure does not mutate the Turn |
| `memory.recall` / `memory.write` | Phase 5 trace spans | Recall/write counts | Same bounded counts | Raw Memory is never printed |
| Recovery projection | RecoveryProjector/StateStore | Durable state, previous Turn, `NEW` next Turn | Same with checkpoint/journal/unknown IDs bounded | UNKNOWN is a visible block/warning; replay disabled |
| Trace evidence | Phase 6 TraceStore | Run, artifact reference, span count | Same full bounded lookup reference | Missing trace is reported as unavailable, not fabricated |

Quiet mode suppresses progress and tool blocks while retaining final Result,
fatal failure text and Evidence where available. No ANSI escape sequence is
required for correctness.

## 8. Tool Rendering Contract

Tool presentation is sourced from the existing Tool Runtime event path:

~~~
ToolRegistry / EffectJournal
  -> AgentLoop on_tool callback
  -> ToolEvent START / COMPLETE
  -> Scheduler sink / DeliveryHub
  -> CliOutlet
~~~

The Phase 7 `ToolEvent` addition is limited to optional receipt observations
(`effect_id`, `effect_status`, `failure_category`) placed after all existing
fields to preserve positional compatibility. The AgentLoop obtains the effect
receipt from the existing journal when available and classifies only the
existing bounded Tool result vocabulary for display. The CLI does not create a
receipt.

The observed normalized Tool categories are:

~~~
tool_validation_failure
tool_timeout
tool_effect_unknown
tool_execution_failure
~~~

The output rules are:

- `effect_status == committed` may render `OK committed`;
- a successful non-mutating completion may render `OK completed`;
- a failed event renders `FAIL <category>`;
- `effect_status == unknown` renders `? effect outcome unknown`, never
  `OK committed`;
- arguments, previews and effect IDs are bounded and sensitive-shaped argument
  keys are redacted;
- large results show a bounded preview/truncated marker rather than flooding
  the terminal;
- assistant prose cannot upgrade a Tool result to success.

The direct contract test constructs an UNKNOWN completion whose preview claims
success and verifies that the CLI still does not render `OK committed`. The
real happy and validation-failure CLI tests exercise the production Tool
events.

## 9. Context and Memory Rendering Contract

The CLI reads the already persisted Phase 4/5 observations from the durable
Run records. It does not reassemble Context or query Memory for display.

Normal output includes, when the Run contains the relevant spans:

~~~
Context
  estimated: <bounded count>
  limit: <bounded count>
  compacted: <bounded count>
  dropped: <bounded count>
~~~

Verbose output additionally shows the existing `context.input_budget` value.
The reporter adds counts from `context.assemble` and `context.compact` records
without dumping the prompt, context segments or chain-of-thought.

Memory output is similarly bounded:

~~~
Memory
  recalled: <count>
  writes: <count>
~~~

Raw MemoryStore records, unrelated repository/user Memory and full Provider
messages are not printed. A Run with no Memory boundary does not receive a
fabricated Memory section.

## 10. Terminal Outcome Contract

The final label is derived from lifecycle and durable Runtime facts:

| Runtime facts | CLI label |
|---|---|
| `TurnEnded`, no Tool failures | `COMPLETED` |
| `TurnEnded` with Tool failures or root `completed_with_tool_failure` | `COMPLETED_WITH_TOOL_FAILURE` |
| `TurnFailed`, not cancelled and not a budget failure | `FAILED` |
| `TurnFailed(cancelled=True)` | `CANCELLED` |
| interrupted Recovery marker | `MAX_ITERATIONS` |
| `TurnFailed` with root `context_budget_failure` | `BUDGET_EXCEEDED` |

The terminal mapping consumes `TurnEnded`/`TurnFailed`, root Trace attributes
and the existing Recovery marker. It never searches the final natural-language
reply for words such as “done”, “success” or “failed”. A missing lifecycle
event with no outcome remains a failure/unavailable condition rather than a
success guess.

## 11. Resume UX Contract

The existing `--resume` and `--continue` selectors remain the public entry
points. When durable state exists, the reviewer-facing output is:

~~~
Recovery
  durable state: available
  previous Turn: turn-<dynamic> (interrupted)
  next Turn: NEW (fresh invocation)
  checkpoint: none
  effect journal: available
  unknown effects: none
  automatic replay: disabled
~~~

If the projection contains unresolved effects, the IDs are bounded and the
output explicitly states that automatic replay is disabled. Recovery state is
read through the existing `RecoveryProjector`/`RecoveryStateStore`; the CLI
does not resurrect the old process or replay an UNKNOWN effect.

The real CLI boundary test configures one Tool iteration, runs Run A to the
existing interrupted marker, ends that invocation's runtime loop, then invokes
`pico run --resume` with the same durable session. The second invocation reads
Recovery, creates a distinct Turn ID and completes with the continued
deterministic Provider answer.

## 12. Trace Discovery Contract

Phase 6 TraceStore remains the durable evidence authority. The CLI now keeps
the two existing roots distinct:

~~~
Workspace state directory
  -> Session / Recovery / EffectJournal state

Tracing config state directory
  -> TraceStore logs/runs/<run-id>.jsonl
~~~

At turn completion the reporter executes the existing lookup sequence:

~~~
displayed Turn ID
  -> TraceStore.find_run_id(turn_id)
  -> TraceStore.run_path(run_id)
  -> TraceStore.read_trace(run_id)
  -> TraceStore.trace_summary(run_id)
  -> Evidence { run, trace, span count }
~~~

The happy-path test extracts the printed Evidence Run ID, opens the durable
JSONL with `TraceStore`, and asserts the actual `spine.turn`,
`context.assemble`, `llm.call` and `tool.call` records exist and contain a
completed root outcome. This proves the displayed Run ID resolves to the
underlying Phase 6 artifact.

## 13. Streaming, Bounds and Machine-Readable Surface Audit

The shipping `pico run` path continues to use `AgentTurnRunner(stream=False)`;
the response remains one bounded `Text` deliverable while Tool events stay
distinct from assistant text. Provider streaming was not redesigned.

The new helper uses bounded single-line text, bounded/redacted argument JSON,
bounded previews and existing TraceStore summaries. It does not create a
second truncation policy for the Runtime or Trace stores.

No large JSON/structured CLI API was invented. The human-readable output is
the Phase 7 priority, and its Evidence section points at the existing durable
JSONL rather than serializing console decorations as a new protocol.

## 14. Windows Compatibility

The tested environment is Windows PowerShell with Python 3.12.14. The
reviewer-facing path remains understandable when output is redirected or
non-color:

- the renderer emits plain text blocks and does not require ANSI;
- `--verbosity quiet` suppresses progress while retaining Result/Evidence;
- Tool, failure, Recovery and Evidence labels do not depend on terminal color;
- bounded text replaces newlines in single-line arguments/previews;
- the deterministic fixture adjusts the child-shell PATH so its
  `python -m pytest` uses the same supported 3.12 environment;
- the baseline script prefers the repository `.venv`/adjacent baseline Python
  before falling back to `python`.

The Phase 7 test file runs through Typer's actual command path on this Windows
runner and asserts no ANSI escape sequence in the happy/quiet output. This is
evidence for the tested Windows PowerShell path, not a claim of universal
cross-platform or accessibility compliance.

## 15. Deterministic Demo Fixture

The local fixture is:

~~~
tests/fixtures/phase7_demo/
  README.md
  calculator.py
  test_calculator.py
~~~

`calculator.py` contains the known defect `return left - right`; the invariant
test expects the sum. `_DeterministicDemoProvider` is the only scripted part of
the happy path. It returns real Tool calls in sequence:

~~~
grep -> read_file -> write_file -> exec python -m pytest -q test_calculator.py -> final Text
~~~

The CLI, runtime assembly, Scheduler, AgentTurnRunner, AgentLoop, ToolRegistry,
EffectJournal and TraceStore remain real. No network, paid Provider or fake
whole-transcript renderer is used. A separate deterministic Provider drives the
validation-failure case, and a bounded-iteration Provider drives the real
Recovery boundary case.

## 16. Happy-Path Transcript

Captured from the real Typer command with dynamic IDs/paths elided:

~~~
PICO Run
Session  cli:<dynamic>
Run      pending
Task
  Find and fix the calculator bug, then run its test.
Turn     turn-<dynamic>
Agent
  grep("return left - right")
Tool
  grep return left - right calculator.py
Tool
  grep return left - right calculator.py
  OK committed (66 ms)
  effect: committed
  effect_id: effect-<dynamic>
  preview: calculator.py:4: return left - right
Tool
  read_file calculator.py
  OK committed (24 ms)
  effect: committed
Tool
  write_file calculator.py
  OK committed (29 ms)
Tool
  exec python -m pytest -q test_calculator.py
  OK committed (810 ms)
  preview: . [100%] 1 passed ... Exit code: 0
Pico
  Fixed calculator.py and verified the focused test.
Run      trace-<dynamic>
Context
  estimated: 4403
  limit: 65536
  compacted: 0
  dropped: 0
Provider
  selected: fixture
  model: fixture/phase7-deterministic
Memory
  recalled: 0
  writes: 0
Result
  COMPLETED
Evidence
  run: trace-<dynamic>
  trace: ...\logs\runs\trace-<dynamic>.jsonl
  spans: 15
~~~

The fixture asserts that the file changed to `left + right`, the focused test
passed, the output contains the actual Tool names and committed receipts, the
terminal state is `COMPLETED`, and the Evidence Run ID opens the durable Trace.

## 17. Failure Transcript

The deterministic Provider-failure path retains a useful failure detail and
durable evidence instead of collapsing to “Something went wrong”:

~~~
PICO Run
Session  cli:<dynamic>
Run      pending
Task
  demonstrate provider failure
Turn     turn-<dynamic>
Failure
  Turn failed: provider offline
Run      trace-<dynamic>
Failure
  category: runtime_internal_failure
Result
  FAILED
Evidence
  run: trace-<dynamic>
  trace: ...\logs\runs\trace-<dynamic>.jsonl
  spans: 1
~~~

The command exits nonzero for this fatal failure. The category shown here is
the existing generic Runtime/Spine fallback for the injected boundary failure;
the real Tool path separately emits the normalized
`tool_validation_failure` category and `COMPLETED_WITH_TOOL_FAILURE` when the
Provider continues after an invalid Tool call.

The real Tool validation transcript is bounded to:

~~~
Tool
  read_file
  FAIL tool_validation_failure
Result
  COMPLETED_WITH_TOOL_FAILURE
Evidence
  run: trace-<dynamic>
~~~

No `OK committed` is rendered for that failed Tool.

## 18. Resume Transcript

Run A reaches the existing interrupted marker at the configured Tool-iteration
boundary:

~~~
PICO Run
Session  cli:20990101_000000_phase7a
Run      pending
Task
  inspect the bug and continue if interrupted
Turn     turn-<dynamic>
Tool
  grep return left - right calculator.py
  OK committed (70 ms)
Pico
  The interrupted run is resumable; this is a new provider turn.
Run      trace-<dynamic>
Result
  MAX_ITERATIONS
Evidence
  run: trace-<dynamic>
~~~

The next `pico run --resume` invocation visibly reads durable state and creates
a new Turn:

~~~
PICO Run
Session  cli:20990101_000000_phase7a
Run      pending
Task
  continue the interrupted task
Recovery
  durable state: available
  previous Turn: turn-<dynamic> (interrupted)
  next Turn: NEW (fresh invocation)
  checkpoint: none
  effect journal: available
  unknown effects: none
  automatic replay: disabled
Turn     turn-<dynamic>
Pico
  The interrupted run is resumable; this is a new provider turn.
Run      trace-<dynamic>
Result
  COMPLETED
Evidence
  run: trace-<dynamic>
~~~

The acceptance test asserts that the resumed Turn ID differs from the prior
Turn ID and that the deterministic Provider was called again. It demonstrates
fresh invocation/runtime-loop semantics; it does not claim exact CPU-process
replay.

## 19. Mainline Bypass Audit

| Required boundary | Evidence that Phase 7 does not bypass it |
|---|---|
| `AgentLoop` | The real happy path calls `assemble_runtime`, constructs the real AgentLoop and submits through `AgentTurnRunner`; the CLI only supplies rendering callbacks. |
| `ToolRegistry` / Tool Runtime | The deterministic Provider emits ToolCall requests, and the real registry executes `grep`, `read_file`, `write_file` and `exec`; the CLI receives actual ToolEvent callbacks. |
| `ContextAssembler` | The real happy test asserts `context.assemble` in the durable Trace; the reporter reads its existing bounded attributes rather than rebuilding a prompt. |
| `Recovery` | The resume test uses the real `prepare_resume`/Recovery state path for the boundary case; the presentation test uses the existing `RecoveryProjector` and checks a distinct new Turn. |
| `Trace` | The CLI resolves the printed Run ID through the real `TraceStore`, opens the durable JSONL and asserts the actual runtime span names and completed root. |

The only injected boundary in the focused fatal-provider test is a small
AgentLoop double to deterministically exercise CLI nonzero/failure rendering.
The happy, Tool validation and real resume tests keep the primary execution
spine real. No CLI-local scheduler, Tool executor, Context builder, recovery
database or trace writer was introduced.

## 20. No Benchmark UI or Evolver Creep

No PicoBench dashboard, leaderboard, chart, experiment explorer or web report
was added. Existing Phase 6 artifacts remain sufficient.

No candidate generation, promotion, activation, rollback, self-improvement or
Evolver UI was added. Phase 7 stops before the Phase 8 relationship:

~~~
pico run -> Trace / Failure Evidence -> [Phase 8 Candidate / Claim Gate]
~~~

## 21. Files Changed

### Production

- `pico/cli/_run_surface.py` — new observation-only verbosity, bounded Tool
  formatting, terminal mapping, Recovery display and Trace evidence reporter.
- `pico/cli/_repl_spine.py` — optional Tool rendering and lifecycle observation
  callbacks threaded through the existing Spine/Hub path; old behavior remains
  available when the optional renderer is absent.
- `pico/cli/agent_commands.py` — `--verbosity`, one-shot/interactive reporter
  wiring, separate Trace/Recovery roots and existing resume presentation.
- `pico/spine/events.py` — optional Tool receipt/category fields appended after
  pre-existing fields for compatibility.
- `pico/agent/loop/main.py` — additive ToolEvent receipt/category enrichment
  from existing Tool Runtime results and EffectJournal; prior Phase 0–6
  changes in this file remain carried-forward work.

### Tests

- `tests/test_phase7_cli_contract.py` — 10 deterministic Phase 7 contracts:
  verbosity/bounds, lifecycle terminal mapping, durable context/provider
  summary, UNKNOWN honesty, real CLI happy path, Tool validation failure,
  fatal failure/exit code, fresh-Turn Recovery, real interrupted/resume
  boundary and quiet/redirect-safe output.

### Fixture

- `tests/fixtures/phase7_demo/README.md`
- `tests/fixtures/phase7_demo/calculator.py`
- `tests/fixtures/phase7_demo/test_calculator.py`

### Docs

- This Phase 7 execution report. No new user-facing architecture or TUI
  document was needed.

### Baseline

- `scripts/run_medium_baseline.ps1` — prefers the supported local Python
  environment and explicitly includes `tests/test_phase7_cli_contract.py` in
  the established Medium+ manifest.

### Report

- `reports/PHASE_07_LUNA_EXECUTION_REPORT.md`

The worktree contains the user-owned carried-forward Phase 0–6 changes. They
were preserved and were not reset, checked out or rewritten as part of Phase 7.

## 22. Verification Evidence

All commands below ran from `<repo-root>` on Windows.

### Phase 7 focused contract

~~~powershell
.venv\Scripts\python.exe -m pytest tests/test_phase7_cli_contract.py -q
~~~

Result:

~~~text
10 passed in 5.55s
~~~

### Focused CLI/Spine/AgentLoop regression

~~~powershell
.venv\Scripts\python.exe -m pytest tests/test_phase7_cli_contract.py tests/test_cli_repl_spine.py tests/test_cli_agent_commands.py tests/test_agent_loop_run_emit.py tests/test_spine_events.py -q
~~~

Result:

~~~text
128 passed in 23.73s
~~~

### Authoritative Medium+ baseline

~~~powershell
.\scripts\run_medium_baseline.ps1
~~~

Result:

~~~text
653 passed in 84.20s (0:01:24)
~~~

The baseline remains an explicit manifest. No unrestricted full-repository
`pytest` command was substituted for it.

### Static/format checks

~~~powershell
.venv\Scripts\python.exe -m py_compile pico\cli\_run_surface.py pico\cli\_repl_spine.py pico\cli\agent_commands.py pico\spine\events.py pico\agent\loop\main.py tests\test_phase7_cli_contract.py
~~~

Result: completed successfully with no output.

~~~powershell
git diff --check
~~~

Result: completed successfully. Git emitted only the repository's existing
LF-to-CRLF working-tree normalization warnings; no whitespace errors were
reported.

~~~powershell
rg -n "PHASE7_TRANSCRIPT" tests/test_phase7_cli_contract.py
~~~

Result: no matches; temporary transcript debug output is absent.

## 23. Claims Proven

Evidence from the tests and current runtime shows:

- `pico run` remains the acceptance/demo surface and preserves existing public
  command options;
- real CLI happy-path execution visibly crosses Turn, Context, Provider,
  search/read, mutation, shell/test, final response, terminal status and
  durable evidence;
- successful Tool Runtime events are visible and include bounded invocation and
  receipt information;
- Tool validation failure is explicit and normalized;
- EffectJournal UNKNOWN is never presented as committed success;
- terminal status follows lifecycle/Runtime evidence, not final prose;
- Context budget/compaction and Memory counts are bounded observations from
  durable Phase 4/5 evidence;
- Recovery output explains previous state versus a fresh resumed Turn and does
  not auto-replay UNKNOWN effects;
- the displayed Run ID resolves through the real Phase 6 TraceStore to JSONL;
- normal/verbose/quiet output remains understandable on the tested Windows
  PowerShell/non-ANSI path;
- the accepted 643-test Phase 6 baseline is preserved, with the explicit Phase
  7 contracts making the current authoritative count 653;
- Phase 8 evolution and activation were not started.

## 24. Claims NOT Proven

The following are intentionally not claimed by Phase 7:

- production UX completeness;
- full-screen TUI quality;
- real LLM task quality;
- real-model determinism;
- cross-platform support beyond tested environments;
- complete accessibility;
- distributed execution UI;
- automatic recovery of UNKNOWN effects;
- automatic self-improvement;
- production Evolver activation;
- Web UI, dashboard or channel UI quality;
- unrestricted provider/network failure coverage;
- distributed tracing or remote telemetry;
- benchmark dashboard or claim-generation behavior;
- exact process replay semantics on resume;
- universal terminal/font/Unicode behavior beyond the tested bounded text path.

## 25. Final Stop Condition

Phase 7 is complete and ready for Web Reviewer inspection. No Phase 8 work was
started.

**Final status: PASS — READY FOR WEB REVIEW**

**STOP → WAIT FOR WEB REVIEW.**
