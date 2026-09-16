# PICO Medium+
## Luna MAX Execution Report — Phase 3

**Report date:** 2026-09-15  
**Repository:** `<repo-root>`<br>
**Working-tree HEAD:** `0ae70289b282bdc808e668bd267c7370ffd0e5b8`  
**Approved demo surface:** `pico run`  
**Authoritative baseline:** `.\scripts\run_medium_baseline.ps1`

## 1. Status

**PASS — READY FOR WEB REVIEW**

Phase 3 implemented and tested the smallest compatible durable recovery
contract for an interrupted local Coding Agent task. `pico run --resume` and
`--continue` now load a read-only recovery projection from durable state before
the existing Scheduler creates a fresh Turn. No prior Python process,
provider, coroutine, Tool object, or Tool execution is resurrected.

The final supported baseline passed **598 tests in 73.29s**. The Phase 3
contract group passed **14 tests** and includes a genuine two-process
fresh-runtime acceptance test. After this report, Phase 3 stops and waits for
Web Review. Context Engine, Memory, Eval, CLI Hardening, and Evolver work was
not started.

## 2. Task and Scope

The recovery unit is one interrupted local task Turn identified by its durable
`(session_key, turn_id)` and the EffectJournal effect IDs produced during that
Turn. Resume means:

1. Resolve the existing Session using the established CLI resolver.
2. Assemble the existing runtime.
3. Read the Session, EffectJournal, Recovery marker, current workspace, and
   optional Checkpoint reference into a bounded `RecoveryState` projection.
4. Construct a **new** `TurnRequest`; Scheduler assigns/propagates a new Turn
   identity.
5. Let the existing AgentLoop and ToolRegistry decide what the new Turn does.

The implementation does not add a replay executor, compensation engine,
distributed workflow, restore command, second Tool registry, or new Session
truth.

## 3. Mandatory Pre-Implementation Audit

The audit was completed before Phase 3 source changes. It covered:

- the full pre-existing `pico/agent/recovery/` draft and
  `tests/test_phase0b1_recovery.py`;
- `pico/session/`, `pico/agent/loop/checkpoint.py`,
  `pico/agent/effects.py`, `pico/agent/loop/main.py`, and
  `pico/agent/tools/registry.py`;
- `pico/spine/`, `pico/cli/agent_commands.py`,
  `pico/cli/_runtime_assembly.py`, and `pico/tracing/`;
- checkpoint, Session, CLI, AgentLoop, EffectJournal, and tracing regression
  tests.

The pre-existing recovery draft is classified **PARTIAL REUSE**. Its immutable
candidate model, read-only local observation collector, pure planner, and
fail-closed scanner were retained. It was not treated as an authoritative
runtime until connected to durable Session/Effect/Checkpoint sources.

Existing `CheckpointService` is classified **ADAPT/COORDINATE**, not rebuilt:
it remains an out-of-band per-Turn shadow-Git filesystem snapshot. Existing
Session persistence is classified **KEEP** for conversation history and
**ADAPT** only through an external recovery marker. Existing EffectJournal and
ToolRegistry lifecycle semantics are **KEEP**. Existing CLI resolver,
RuntimeAssembly, Scheduler, and AgentLoop are **KEEP**, with one narrow resume
hook.

## 4. Current vs Final Recovery Map

| Boundary | Before Phase 3 | Final Phase 3 contract |
|---|---|---|
| Session | Durable messages, metadata, consolidation and clarification state; no recovery projection | Still owns only conversation history; projector reads it with `peek()` and never stores effect facts there |
| Workspace | Current files; local-write hashes available only through Effect/observation helpers | Read-only reconciliation compares current existence/hash to durable pre/post expectations; conflict is `ASK_HUMAN` |
| Checkpoint | Shadow-Git snapshot at normal/max-iteration Turn end; same-process recovery hint only; no restore | Marker records only checkpoint ID/files; fresh runtime inspects the reference and availability; no automatic restore or dirty-worktree overwrite |
| EffectJournal | Append-only `PREPARED/RUNNING/COMMITTED/FAILED/UNKNOWN` lifecycle | Remains the authoritative effect fact source; latest records are filtered by Session and re-planned on every resume |
| Recovery draft | Read-only scanner/planner with no runtime projection or CLI connection | Reused by `RecoveryProjector`; no execution is added |
| Recovery State | No separate durable state | Atomic per-Session marker at `state/recovery/session-<sha256>.json`; only last Turn/checkpoint metadata, no effect payloads |
| CLI `--resume` | Resolved and bound a Session but did not inspect durable recovery | Resolves Session, assembles runtime, invokes read-only `prepare_resume`, then follows the existing new-Turn path |
| Trace | Session/Turn/Tool evidence, but no recovery-specific evidence | `recovery.resume` span attributes record continuation, Session/task, durable inspection, UNKNOWN effects, checkpoint reference use, prior Turn, and new Turn |

## 5. State Ownership Matrix

| Owner | Durable facts | Writes in Phase 3 | Explicitly does not own |
|---|---|---|---|
| SessionManager / Session | User, assistant, and tool conversation history plus existing Session metadata | Existing JSONL persistence only | Effect lifecycle, checkpoint commit/restore, recovery decisions |
| Workspace | Current file bytes/existence and dirty state | Existing Tools may mutate it during a normal new Turn | Historical effect certainty or external side-effect receipts |
| CheckpointService | Shadow-Git filesystem snapshot, commit ID, changed file list | Existing end-of-Turn best-effort shadow commit | Conversation state, per-Tool attribution, restore/conflict resolution |
| EffectJournal | Immutable Tool effect identity, lifecycle, hash pre/post facts, result/receipt references | Existing Registry lifecycle append | Tool arguments/result payload recovery, automatic replay |
| RecoveryStateStore | Last Turn status/ID and optional Checkpoint reference/files | Atomic marker replacement | Effect facts; it is an index/hint, not a second effect truth |
| RecoveryProjector | Read-only derived Session/effect/observation/Checkpoint view | Nothing | Provider calls, Tool calls, workspace writes, replay, restore |
| TraceStore | Local audit spans and attributes | Existing `recovery.resume` span emission | Business truth, recovery authorization, delivery success |

## 6. Resume Contract

The supported CLI path is:

```text
pico run --resume <id-or-prefix>
        -> resolve existing Session
        -> existing RuntimeAssembly
        -> AgentLoop.prepare_resume(session_key)
        -> RecoveryProjector.project(session_key) [read-only]
        -> existing TurnRequest / Scheduler lane
        -> new turn_id
        -> existing AgentLoop/context/provider/ToolRegistry
```

The projection is injected once into the new user message as bounded evidence.
It states that the previous process/provider/coroutine/Tool is not resumed.
`--continue` uses the same projection when it finds an existing recent CLI
Session. `--session` remains a pure Session binding selector for compatibility;
it does not silently change into recovery mode.

Every known AgentLoop Turn outcome writes the small marker. If marker writing
fails, the Turn is not changed into an error; the EffectJournal remains the
authoritative source for any durable effect fact. A process crash before the
marker write is therefore still inspectable through the EffectJournal and
workspace evidence, with a missing-marker warning.

## 7. Effect Recovery Matrix

| Latest durable state | Projector decision | Automatic replay | New Turn behavior |
|---|---|---:|---|
| `PREPARED` | Candidate remains unresolved; READ may be explicitly retryable, opaque effects require human attention | No | Explain the pre-execution boundary; do not execute the old call |
| `RUNNING` | Candidate remains unresolved; local/READ planner evidence is evaluated | No | Inspect current evidence; any retry belongs to the new Turn |
| `COMMITTED` with durable result/receipt | `NO_REPLAY`, resume with observation | No | Use the durable observation/reference; no blind Tool call |
| `COMMITTED` without observation | `NO_REPLAY`, observation unavailable/attention required | No | Do not fabricate a result or re-execute |
| `FAILED` | Terminal failure remains a terminal fact; planner never schedules automatic retry | No | Surface the failure; a new Turn may make an explicit decision |
| `UNKNOWN` | UNKNOWN is preserved; opaque effects ask for human decision; supported local hash evidence may say explicit retry is allowed | **Never** | Surface effect ID and evidence; never convert UNKNOWN to FAILED or replay automatically |
| Local write current hash = post hash | `NO_REPLAY` | No | Treat the write as already evidenced, without running it again |
| Local write current hash = pre hash | Explicit `RETRY_ALLOWED` for the supported local-write case | No | New Turn may intentionally issue a new write after inspecting state |
| Local write matches neither hash | `ASK_HUMAN` conflict | No | Do not overwrite a changed/dirty workspace |

`RecoveryState.automatic_replay_effect_ids` is deliberately always empty.
Planner language such as `RETRY_ALLOWED` means only that an explicit new Turn
could choose that action; it is not an execution instruction.

## 8. Crash-Window Audit

| Crash window | Durable fact available | Recovery result |
|---|---|---|
| Before Tool execution | `PREPARED` may be present | Candidate is projected; no old invocation is resumed |
| After execution gate / before completion | `RUNNING` may be present | Candidate is projected; READ/local evidence can be inspected, opaque effects remain conservative |
| After Tool completion / before terminal append | Last durable state remains `RUNNING` or `UNKNOWN` according to the existing Registry boundary | No false `FAILED` conversion; no automatic replay |
| Terminal append durable | `COMMITTED` or `FAILED` is authoritative | COMMITTED is not blindly re-executed; FAILED is not auto-retried |
| Local filesystem write completed but process/append failed | Current file hash can match pre or post expectation | Post match skips replay; pre match permits only explicit retry; conflict asks human |
| Recovery marker write failed | EffectJournal and workspace may still be available | Projection reports missing marker and derives what it can from authoritative sources |

EffectJournal history validation remains fail-closed. A corrupt complete
journal is not partially scanned into a recovery decision.

## 9. Checkpoint and Workspace Contract

`CheckpointService` still snapshots filesystem state in a separate shadow Git
directory and does not touch the user's `.git`. It is not a transaction log and
does not contain Session history, Tool-call attribution, or external receipts.

Phase 3 only checks a recorded checkpoint reference against the expected shadow
repository `HEAD` when that path is known. The trace and prompt distinguish
`checkpoint_used` (reference inspected) from `checkpoint_restored=false`.
There is no restore API or automatic restore. A dirty or changed workspace is
observed through current hashes; it is never overwritten by recovery loading.

## 10. Failure Matrix

| Durable Inputs | Recovery Decision | Replay Allowed? | New Turn Behavior | Evidence / Test |
|---|---|---:|---|---|
| Existing Session; marker `completed`; no unresolved effects | Clean continuation | No automatic replay | Reuse Session history in a new Turn | `test_clean_completed_turn_is_projected_without_recovery_work` |
| Session + `PREPARED` READ | Explicit retry may be considered | No automatic replay | Explain that Tool code was not proven to run | `test_effect_state_recovery_is_conservative[...]` |
| Session + `RUNNING` READ | Explicit retry may be considered | No automatic replay | Treat previous execution as non-resurrectable | `test_effect_state_recovery_is_conservative[...]` |
| Session + `COMMITTED` READ/result reference | `NO_REPLAY` / resume observation | No | Use durable result reference; no old Tool call | same parameterized test; Phase 0B.1 tests |
| Session + `FAILED` opaque effect | `ASK_HUMAN` | No automatic retry | Show failure and require a new decision | same parameterized test |
| Session + `UNKNOWN` opaque effect | `ASK_HUMAN`; preserve UNKNOWN | Never automatic | Show effect ID and do not replay or relabel | `test_effect_state_recovery_is_conservative[...]`; trace test |
| Local write current bytes match post hash | `NO_REPLAY` | No | Continue after evidence inspection | `test_local_write_hash_evidence_proves_retry_and_post_skip` |
| Local write current bytes match pre hash | Explicit retry allowed by planner | No | New Turn may intentionally issue a new write | `test_local_write_hash_evidence_proves_retry_and_post_skip` |
| Local write current bytes match neither pre nor post | `ASK_HUMAN` conflict | No | Preserve third-party/dirty change | `test_local_write_reconciles_post_pre_and_conflict_without_writing` |
| Recovery marker absent | Derive from Session/EffectJournal; warn | No | Continue with bounded missing-artifact evidence | `test_missing_and_corrupt_recovery_artifact_fail_closed_but_derive_effects` |
| Recovery marker malformed or wrong schema | Fail closed for marker; derive authoritative effect facts | No | Do not trust marker metadata | same artifact test |
| Session absent | Report `session_missing`; do not create it during projection | No | CLI resolver rejects normal `--resume`; direct projector reports missing | `test_missing_session_and_checkpoint_are_reported_without_creating_session` |
| Marker references missing Checkpoint `HEAD` | Report `checkpoint_missing`; no restore | No | Continue only with current workspace/effect evidence | same missing Session/Checkpoint test |
| Fresh Runtime B after Runtime A exits | Load durable files in a new process and mint a new Turn ID | No | Project state, then run existing new-Turn path | `test_fresh_runtime_process_loads_durable_state_and_constructs_new_turn` |

## 11. Cross-Process Evidence

`test_fresh_runtime_process_loads_durable_state_and_constructs_new_turn` starts
one Python process to persist a Session, a `PREPARED` EffectJournal record, and
an interrupted Turn marker. That process exits. A separate Python process then
constructs a new `SessionManager` and `RecoveryProjector`, loads the durable
state, verifies the `retry_allowed` planner explanation with an empty automatic
replay set, and constructs `TurnRequest(turn_id="turn-runtime-b")` while the
durable previous Turn is `turn-runtime-a`.

No Runtime A object, provider, coroutine, Tool instance, or in-memory projection
is passed to Runtime B. This is the required fresh-runtime evidence; it does
not claim that an LLM will make the same decision or that an arbitrary external
side effect is reversible.

## 12. Trace Evidence

An explicitly prepared resume emits a local `recovery.resume` span as a child
of the new Session Turn context. Its bounded attributes include:

- `recovery.resumed_continuation`, `recovery.task`, and `recovery.session_key`;
- `recovery.durable_state_inspected` and source/artifact statuses;
- `recovery.unknown_effect_ids`;
- `recovery.checkpoint_id`, `recovery.checkpoint_used`,
  `recovery.checkpoint_use=reference_only`, and
  `recovery.checkpoint_restored=false`;
- `recovery.previous_turn_id`, `recovery.new_turn`, and `recovery.new_turn_id`.

`test_recovery_trace_evidence_carries_session_task_effect_checkpoint_and_new_turn`
asserts the evidence shape. Tracing remains best-effort and does not become a
recovery truth source.

## 13. Idempotency Boundary

The Phase 3 boundary is conservative and explicit:

- Recovery marker paths are keyed by a SHA-256 digest of the Session key and
  atomically replaced through the existing locked storage primitive.
- Effect identity and lifecycle remain immutable in the existing journal;
  projection collapses only the latest record per `effect_id`.
- Candidate, observation, plan, and projection IDs are derived from stable
  semantic facts, excluding volatile timestamps and payload contents.
- `COMMITTED`/`FAILED`/`UNKNOWN` never enter an automatic execution queue; the
  new Turn must explicitly decide any action.
- The design does not claim exactly-once behavior. A new explicit Tool call is a
  new attempt, not a hidden continuation of an old coroutine.

## 14. CLI and Compatibility Audit

Existing `--session`, `--continue`, and `--resume` mutual exclusion and Session
resolution behavior are preserved. Invalid `--resume` still stops before
runtime assembly. The only supported recovery wiring is an optional
`prepare_resume` method on the already assembled AgentLoop; legacy test doubles
without that method continue to work. No new public CLI command or incompatible
Session key format was introduced.

## 15. Test Evidence

### Phase 3 focused contract

```text
python -m pytest -q tests/test_phase3_recovery_contract.py
14 passed in 3.05s
```

The group covers clean completion, PREPARED/RUNNING/COMMITTED/FAILED/UNKNOWN,
local pre/post/conflict reconciliation, missing/corrupt markers, missing
Session/Checkpoint, AgentLoop injection, marker ownership, trace evidence,
and the cross-process fresh-runtime acceptance path.

### Authoritative Medium+ baseline

```powershell
.\scripts\run_medium_baseline.ps1
```

Final result:

```text
598 passed in 73.29s (0:01:13)
```

This is the accepted Phase 2 baseline of 584 tests plus the 14 deliberate
Phase 3 contract tests. The Phase 3 test file is explicitly listed in the
PowerShell manifest and documented in `docs/medium-plus-baseline.md`.

### Static checks

```text
ruff check <Phase 3 source and test files>  -> All checks passed!
git diff --check                            -> no whitespace errors
```

The separately run optional deep Checkpoint audit remains outside the accepted
manifest and produced the two known Windows-environment failures already
identified in earlier audit work: creating a symlink requires the Windows
privilege (`WinError 1314`), and Windows filesystem mode handling does not
expose the POSIX chmod-only Git diff expected by that test. No Phase 3 source
change was made for either deferred platform assumption.

## 16. Files Changed

### Phase 3 production source

- `pico/agent/recovery/state.py` — atomic per-Session Recovery marker store;
  marker contains only Turn/checkpoint metadata.
- `pico/agent/recovery/projector.py` — read-only Session/Effect/Workspace/
  Checkpoint projection, conservative plan aggregation, prompt and trace
  evidence.
- `pico/agent/recovery/__init__.py` — exports the Phase 3 recovery contract.
- `pico/agent/loop/main.py` — constructs the projector, persists known Turn
  markers, supports `prepare_resume`, injects bounded evidence, and emits the
  recovery trace span.
- `pico/cli/agent_commands.py` — wires existing `--resume`/existing-session
  `--continue` into the optional read-only prepare hook.

### Phase 3 tests and acceptance harness

- `tests/test_phase3_recovery_contract.py` — 14 focused contract tests,
  including the two-process acceptance test.
- `tests/test_cli_agent_commands.py` — extends the existing resume test to
  assert the recovery prepare hook while preserving Session binding.
- `scripts/run_medium_baseline.ps1` — explicitly includes the Phase 3 test
  file.
- `docs/medium-plus-baseline.md` — documents the Phase 3 inclusion and
  conservative replay semantics.
- `reports/PHASE_03_LUNA_EXECUTION_REPORT.md` — this report.

Pre-existing Phase 0/1/2 changes and untracked artifacts in the worktree were
preserved. In particular, the Phase 0B.1 recovery draft files were audited and
reused but not rewritten into an execution engine.

## 17. Claims Proven

- The supported `pico run` baseline passes 598 tests after Phase 3 changes.
- A durable recovery marker can be atomically written and loaded without
  copying EffectJournal facts into Session or marker state.
- A fresh process can load Session/Effect/Recovery state after the writer
  process exits and construct a different Turn identity.
- PREPARED and RUNNING windows remain visible; terminal and UNKNOWN states are
  interpreted conservatively.
- COMMITTED effects are not blindly re-executed; FAILED effects are not
  automatically retried; UNKNOWN effects are never automatically replayed.
- Supported local-write post/pre/conflict hash evidence is read-only and does
  not overwrite the workspace.
- Missing/corrupt recovery markers, missing Sessions, and missing Checkpoint
  references are reported without fabricating state or creating a Session during
  projection.
- `pico run --resume` uses the existing runtime assembly and Scheduler path and
  creates a new Turn rather than resurrecting an old runtime object.
- Recovery trace evidence records the continuation, Session/task, durable
  inspection, UNKNOWN effects, Checkpoint reference-only use, and new Turn.

## 18. Claims NOT Proven

- Exactly-once execution.
- Automatic safe replay of arbitrary side effects.
- Distributed recovery.
- Deterministic LLM replay.
- Process resurrection.
- Full transactionality across workspace and external effects.
- Zero-loss recovery.
- Successful restore/rollback of a Checkpoint or automatic conflict merge.
- Correctness of arbitrary external receipts or remote side-effect queries.
- Full-repository Windows, TUI, channel, Cron, PicoBench, AppWorld, Evolver,
  real VM, or live-provider operation.

## 19. Final Stop Condition

Phase 3 implementation, tests, explicit baseline update, and report are
complete. **STOP — WRITE REPORT — WAIT FOR WEB REVIEW.**
