# PICO Medium+
## Luna MAX Execution Report — Phase 6

**Report date:** 2026-09-15  
**Repository:** `D:\Agent Learning\Pico Agent`  
**Working-tree HEAD:** `0ae70289b282bdc808e668bd267c7370ffd0e5b8`  
**Approved demo surface:** `pico run`  
**Authoritative baseline:** `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_medium_baseline.ps1`

## 1. Status

**PASS — READY FOR WEB REVIEW**

Phase 6 hardens the existing Trace and PicoBench paths into one bounded,
inspectable evidence chain. The implementation keeps the existing
`audit.span.v1`/PicoBench architecture and adds durable per-run trace JSONL,
bounded/redacted payload observation, Run/Turn correlation, explicit
Task/Trial/Verifier evidence fields, measurement-validity accounting, paired
comparison evidence, deterministic verifier sealing, layered Claim Gates and a
bounded reproducibility manifest.

The final authoritative baseline passed **643 tests in 83.29s**. The focused
Phase 6 contract file passed **14 tests in 4.75s**. The final baseline includes
the Phase 6 contract file in its explicit test manifest, so the increase from
the accepted Phase 5 result of 629 tests is deliberate and auditable.

Phase 1 Runtime, Phase 2 Tool Runtime, Phase 3 Recovery, Phase 4 Context and
Phase 5 Structured Memory remain frozen and green in the authoritative
manifest. No Phase 7 CLI hardening, Phase 8 evolution loop, UI/TUI/channel/Cron
work, remote telemetry backend, Memory redesign, Context redesign, Recovery
redesign or real-model optimization claim was started.

After this report: **STOP → WAIT FOR WEB REVIEW**.

## 2. Objective and Scope

The Phase 6 objective was evidence credibility, not log volume:

```text
Task Result
    !=
Measurement Validity
    !=
Claim Eligibility
```

The supported evidence path now makes it possible to answer, from structured
artifacts, what ran, which Run/Turn/Tool boundary was involved, what the
deterministic Verifier observed, whether the Trial was a usable measurement,
and why a claim was supported, rejected or inconclusive.

The work reused the existing `pico/tracing`, `benchmarks/picobench` harness,
ArtifactStore, plan/pair/reducer/report path, existing RuntimeTrialHost and
existing AgentLoop/Spine path. No `PicoBenchV2`, `TraceEngineV2`,
`EvaluationPlatform` or second verifier framework was introduced.

## 3. Mandatory Pre-Implementation Audit

The shipping symbols and paths were inspected before changing the design:

| Area | Existing implementation found | Phase 6 decision |
|---|---|---|
| `pico/tracing/` | Lazy `TraceStore`, `audit.span.v1`, nested `trace.span`, semantic-convention extractors, active JSONL logs and out-of-line artifacts | Keep the single Trace core; add durable per-run projection and final store-level bounds |
| `pico/spine/` and `pico/agent/loop/` | Scheduler-owned `spine.turn` root, AgentLoop `session.turn`, Context/Provider/Tool/Memory/Recovery instrumentation, explicit terminal lifecycle | Preserve Scheduler/TurnOutcome authority; correlate through existing request `turn_id` and the TraceStore index |
| `pico/context_engine/` | Existing ContextAssembler budget/structure gate and context decision metadata | Add no new context engine; retain budget/keep-drop-compact evidence |
| `pico/memory_engine/` | Phase 5 single MemoryStore with `memory.write`/`memory.recall` boundaries and lifecycle policy | Add bounded trace observation only; keep MemoryStore as Memory authority |
| `pico/agent/effects/` | EffectJournal owns side-effect state and UNKNOWN/COMMITTED semantics | Trace records observation/category; it does not assert an effect commit |
| `benchmarks/picobench/` | `Pack`, `TaskSpec`, `TrialContext`, `TrialExecution`, ArtifactStore, plan/pairs, reducer, report and existing verifier seams | Extend existing records and harness; do not create a parallel framework |
| `pico/eval/` | No separate shipping package in the repository | Do not invent one; use the actual `benchmarks/picobench` path |
| `pico/cli/` | `pico run` assembles the existing Runtime/AgentLoop path | Do not force artificial CLI-to-PicoBench coupling |
| Windows lock path | PicoBench modules directly depended on POSIX `fcntl` in local lock paths | Route those exact locks through the existing `pico.utils.portable_lock` abstraction |

The audit also found that `TurnOutcome` is a frozen public compatibility
carrier. An initial correlation attempt added fields to it, and the
authoritative regression immediately rejected that change. The final design
does not alter its field set; RuntimeTrialHost resolves the durable Run by the
already existing request `turn_id`.

## 4. Current vs Final Trace Map

| Boundary | Before Phase 6 | Final Phase 6 contract |
|---|---|---|
| Run identity | Trace IDs existed inside nested spans, but there was no durable per-Run file/index used by Trial hosts | The root `spine.turn` Trace ID is the Run ID; each span carries `run.id`, and `TraceStore` persists `logs/runs/<trace-id>.jsonl` |
| Turn identity | Request/semantic fields were available in parts of the path; failed host results had no uniform Trace reference | `turn.id` is carried from the Scheduler root into child spans; `RuntimeTrialHost` resolves both successful and failed/cancelled Runs by `turn_id` |
| Span hierarchy | Existing nested `session.turn`, Provider, Tool, Context, Memory and other spans | Same hierarchy, with root ownership retained and per-run reconstruction/summary added |
| Provider evidence | Existing semantic attributes and artifacts could expose more payload than the final evidence boundary permits | Bounded message views, role/count/digest observations, bounded previews, usage, model/call identity and reasoning presence/size/digest; no raw chain-of-thought |
| Tool evidence | Existing Tool spans and EffectJournal linkage | Tool name, call ID, turn ID, effect ID, bounded input/output and normalized failure category; EffectJournal remains authoritative for effect state |
| Context evidence | Existing ContextAssembler metadata and `context.decisions` artifact | Same Phase 4 budget/structure behavior with bounded durable decisions and explicit limit, kept/dropped/compacted, overflow and estimate fields |
| Memory evidence | Existing Phase 5 Memory spans | Bounded `memory.write` and `memory.recall` decision/identity/diagnostic artifacts, linked to the enclosing Run/Turn |
| Recovery evidence | Phase 3 immutable projection and `RecoveryState.trace_attributes` | Existing projection remains authoritative; `recovery.resume` records bounded session/previous/new-turn/checkpoint/effect observations only |
| Terminal evidence | Root span terminal attributes existed but a host Trial had no standard Trace reference | Explicit `completed`, `completed_with_tool_failure`, `provider_failed`, `error` or `cancelled` attributes plus host/Trial links and durable per-run evidence |
| Artifact bounds | Existing out-of-line persistence and image sanitization were not a uniform final-size policy | Store-level collection/depth/string bounds, sensitive-key redaction, SHA-256/preview envelopes and `TRACE_ARTIFACT_MAX_BYTES` hard cap |
| Viewer/readability | Active audit logs and viewer-compatible span shape | Existing active logs remain; `read_trace()` and `trace_summary()` provide inspectable bounded Run reads without creating a second transcript database |

The durable run file is a local evidence projection, not a replacement for
Session, Memory, EffectJournal, Recovery state or benchmark result artifacts.

## 5. Trace Authority Boundary

| Authority | Owns | Trace may record |
|---|---|---|
| Session/SessionManager | Conversation history and persisted user/assistant turns | Session/turn correlation and bounded lifecycle observations |
| EffectJournal | Effect preparation, running, committed, failed and unknown state; receipt/effect truth | Tool/effect activity, effect ID and bounded result/category; Trace does not convert UNKNOWN into committed |
| RecoveryProjector/RecoveryStateStore | Durable recovery inputs, conservative plan and fresh-Turn projection | `recovery.resume` observation, projection ID, bounded unknown-effect/checkpoint references and new-turn ID |
| MemoryStore | Structured Memory schema, eligibility, scope, lifecycle, persistence and recall truth | `memory.write`/`memory.recall` decision, IDs, digests, counts and bounded previews |
| ContextAssembler | Final context composition, token budget, protected layers, structural validation and overflow behavior | Budget, estimates, keep/drop/compact counts, decision artifact and overflow reason |
| Workspace/isolated fixture | Files and repository state being evaluated | File/task observations; Trace does not certify workspace truth |
| Deterministic Verifier | Task correctness and invariant checks | Verifier identity/digest and a reference to its result artifact |
| PicoBench reducer/report | Denominators, pair validity, measurement validity and claim eligibility | References/digests; it does not treat a Trace span as a benchmark verdict |
| TraceStore | Bounded execution observation and local inspectable Run projection | Only the evidence it writes; it is not any of the authorities above |

In particular, a Trace `tool.call` with `tool.failure_category=tool_effect_unknown`
does not claim that the external effect committed. The Phase 2 EffectJournal
remains the source of truth for that determination.

## 6. Trace Event Matrix

| Boundary | Event/Span | Identity | Payload policy | Artifact |
|---|---|---|---|---|
| Scheduler/Spine | `spine.turn` open/end/fail/cancel | `run.id` = Trace ID, `turn.id`, `session.id`, channel/chat and conversation | Origin, busy policy, terminal state, error class/category, tool counts and latency; no full prompt | Durable per-run JSONL plus active span log |
| Agent/Session | `session.turn` | Same inherited Run/Turn/Session identity | Existing turn seed/open/terminal extraction; bounded artifacts | Per-run JSONL; out-of-line only when an extractor attaches one |
| Context | `context.assemble`, `context.compact`, `context.curate` | Inherited Run/Turn and session key | Context limit, provider limit, input budget, reserved output/margin, estimates, keep/drop/compact counts, reason and structural status | `context.decisions` is bounded to the first 256 decisions; referenced from the span |
| Provider | `llm.call` request/response | Run/Turn, span/call ID, provider/model | System/latest-user/history views are previews, char counts and SHA-256 digests; tool schemas are name/preview/digest; usage is normalized | `llm.input`/`llm.output` are out-of-line and store-bounded; reasoning is presence/chars/digest only |
| Tool runtime | `tool.call` | Run/Turn, `tool.call_id`, `tool.effect_id`, `tool.turn_id` | Tool name, bounded args/result previews, duration, explicit failed flag and normalized categories: validation, execution, timeout or effect unknown | `tool.input`/`tool.output` are bounded and redacted by the TraceStore; EffectJournal owns effect state |
| Memory | `memory.recall` | Inherited Run/Turn, session, repository/project/user where available | Query preview, scope, top-k/max chars, backend, candidates/eligible/selected/stale counts, hit IDs/kinds/scopes/verification/digests | `memory.recall` artifact contains bounded hit previews and identities |
| Memory | `memory.write` | Inherited Run/Turn, item ID/content digest where available | Accepted/action/reason, kind/scope/verification, deduplication and supersession count; no raw candidate text | `memory.write` artifact contains decision and bounded diagnostics only |
| Recovery | `recovery.resume` | Session, previous turn, new turn, projection ID | Durable-input status, checkpoint reference-only state and capped unknown effect IDs | No duplicated recovery database; source remains RecoveryStateStore/EffectJournal |
| Retry/error | Existing `llm.call`, `tool.call` and `spine.turn` error attributes | Same Run/Turn plus Provider/Tool call IDs | Normalized category and bounded error preview; retry/attempt identity remains in existing Provider/PicoBench records | Existing call/error artifacts remain bounded; no raw stack/secret dump |
| Terminal lifecycle | `TurnEnded`/`TurnFailed` represented on root | Run/Turn and explicit lifecycle event | `completed`, `completed_with_tool_failure`, `provider_failed`, `error`, `cancelled`; channel delivery remains separate | Durable root span; host result stores runtime/delivery state separately |
| Evaluation | Trial/Attempt/Report references rather than a new span type | `task_id`, `trial_id`/record key, Run/Turn and verifier identity | Status, measurement validity, artifact refs, verifier result and Claim Gate state | `attempt-record.json`, `trial-record.json`, `verifier-result.json`, `summary.json`, `REPORT.md` |

Not every Run contains every row. The contract is that an executing boundary
emits its existing span/extractor evidence; the mainline and direct Memory/
Tool tests exercise the representative boundaries.

## 7. Bounded Trace and Durable Persistence Contract

`TraceStore` now writes every emitted span to both the existing active span
log and a per-Run JSONL file. Writes are flushed and `fsync`ed while holding a
portable adjacent lock. `read_trace(trace_id)` ignores malformed lines rather
than manufacturing a valid record, and `trace_summary(trace_id)` returns the
schema `pico.trace.summary.v1`, span count, root IDs, terminal outcomes and a
bounded selected-attribute view.

The store-level observation policy is deliberately conservative:

- sensitive-shaped keys such as authorization, cookie, password, token,
  secret, API key and private/hidden reasoning fields become `[REDACTED]`;
- lists/dicts are capped at 64 items and nested values at a fixed depth;
- long strings/bytes become `{chars, sha256, preview, truncated}` envelopes;
- raw Provider messages are reduced to role/count/preview/digest observations;
- reasoning content is never persisted as raw chain-of-thought;
- inline image data continues to be sanitized before artifact persistence;
- persisted artifact bytes are capped by `TRACE_ARTIFACT_MAX_BYTES`, default
  32 KiB, with a digest/preview envelope if the bounded representation still
  exceeds the cap.

These are bounded observability guarantees for the supported local path, not a
claim that every possible secret encoding can be detected.

## 8. PicoBench Current vs Final Map

| Model boundary | Current before Phase 6 | Final Phase 6 contract |
|---|---|---|
| Task | Existing `TaskSpec` payload and Pack-defined success behavior | Task payload explicitly carries fixture identity, success condition, required/forbidden paths and verifier identity; Pack/ExecutionPolicy remain the owner of timeout/budget |
| Trial input | Existing `TrialContext` with experiment/plan/key/task/variant | Same identity plus deterministic isolated workspace execution; no Agent-defined success criterion |
| Trial result | `TrialExecution` status, runtime/delivery state, verification, metrics and refs | Same result plus explicit `measurement_valid`, `run_id`, `turn_id`, trace/verifier/reproducibility references; normalized at the harness edge |
| Attempt/Trial records | Durable status/metrics/artifact fields | New evidence fields are copied into immutable attempt/trial records and checked during resume/rebuild |
| Verifier result | Existing `VerifierResult` state/findings/metrics | Verifier ID, code/bundle digest and artifact references are retained; `run_sealed_verifier` rejects changed seals, crashes and malformed returns |
| Verifier implementation | Existing JSON/pack verifier seams | Existing seam extended with `RepositoryTaskVerifier`; baseline file digests, required changed paths, forbidden unchanged paths and bounded `pytest` execution are independently inspected |
| Baseline/candidate | Existing variants, PairSpec, PairResult and comparison blocks | Phase 6 fixture uses the existing pair path for the same task/fixture/verifier; no Evolver activation is involved |
| Measurement | Status classes could be mistaken for a complete denominator | Valid, invalid and unaccounted counts are explicit; reducer metrics include `trial.planned`, `accounted`, `valid`, `invalid`, `unaccounted`, `task_passes` and `task_failures` |
| Claim | Existing per-metric rule evaluation | Final `positive_claim_eligible` comes from the layered Claim Gate; per-rule observations cannot bypass measurement, integrity, regression or evidence gates |
| Report | Existing summary/Markdown/reduction output | FullReport carries ClaimState, reason and gate booleans; summary/`cv-metrics.json`/Markdown expose the final state |
| Reproducibility | No uniform representative fixture manifest | `pico.picobench.reproducibility.v1` manifest is written beside the fixture verifier result |

The additions are extensions of the existing classes and paths. No duplicate
evaluation framework or second report authority exists.

## 9. Windows Portability Change

### Old failure

The Phase 0 audit found direct POSIX `fcntl` lock dependencies in the local
PicoBench artifact, Provider budget and semantic campaign paths. That made the
supported Windows import/execution path fail or lose the intended locking
boundary.

### Replacement and preserved semantics

The affected paths now use the repository's existing narrow
`pico.utils.portable_lock.file_lock` abstraction, which delegates to
`portalocker`:

- POSIX retains exclusive advisory file locking through the platform backend;
- Windows uses the platform's `LockFileEx` behavior through `portalocker`;
- blocking locks remain blocking;
- non-blocking run/semantic locks still raise the project-level
  `LockTimeoutError` and are translated to the existing conflict errors;
- retry-claim and Provider ledger critical sections still hold one lock for
  the full read/check/write mutation;
- lock files remain local adjacent anchors; locking was not removed and no
  distributed lock service was introduced.

The adjacent atomic-artifact behavior was also made Windows-safe: directory
`fsync` is best-effort where Windows cannot fsync a directory handle, hard-link
installation falls back to exclusive create when necessary, and long
PicoBench artifact paths use Windows extended-length I/O while retaining the
full SHA-256 experiment ID. This keeps direct `ref.root / ...` inspection
usable without truncating experiment identity.

Evidence: `test_portable_artifact_and_budget_locks_have_one_cross_platform_path`
passes on the actual Windows runner, the Phase 6 PicoBench fixture executes on
Windows, no direct `fcntl` import remains in the supported PicoBench/Trace
paths, and the final authoritative baseline is green.

## 10. Task / Trial / Verifier Contract

The representative fixture is `phase6-calculator-v1`:

```text
Task: calculator-add
Fixture: a tiny Python repository with calculator.py and test_calculator.py
Success: pytest passes, calculator.py changed, test_calculator.py unchanged
Verifier: repository_task_v1
Pair: baseline strategy vs candidate strategy
```

`Phase6RepositoryPack.definition()` expresses that contract as existing
`TaskSpec`, `VariantSpec` and `PairSpec` objects. `TrialContext` carries the
experiment, plan, task, variant, repetition and block attempt. Each
`TrialExecution` returns a task status, runtime/delivery state, independent
`VerifierResult`, observed settings, metrics, findings, artifact references and
an explicit validity/evidence link set.

`RepositoryTaskVerifier.capture()` records baseline SHA-256 digests before the
strategy runs. `verify()` then checks the required source edit, forbidden test
invariant and a bounded `sys.executable -m pytest` subprocess. Its result is
created from workspace/test observations. The fixture's deliberately false
baseline self-report (`"fixed"`) is stored only as contrast telemetry; it is
not passed to the verifier and cannot change `VerificationState.FAILED`.

`VerifierSeal.capture_many()` seals the task test definition. The sealed
wrapper checks the seal before and after verification, attaches verifier ID and
digest, returns `NOT_RUN` for a changed seal/crash/invalid return, and preserves
`asyncio.CancelledError` rather than converting cancellation into a pass or
failure claim.

## 11. Measurement Validity Contract

Task status and measurement validity are independent fields:

| Situation | Task/Trial result | Measurement validity | Denominator behavior |
|---|---|---:|---|
| Verifier passes | `PASSED` | `true` | Valid task pass |
| Verifier runs and rejects workspace/tests | `TASK_FAILED` | `true` | Valid task failure |
| Verifier crashes, seal changes or returns malformed output | `INFRASTRUCTURE_FAILURE`/`NOT_RUN` | `false` | Counted as invalid, not an ordinary task failure |
| Harness times out or pack raises before a usable result | Operational failure | `false` | Counted as invalid when a Trial record exists |
| Explicit `measurement_valid=false` | Underlying task status is retained for diagnosis | `false` | Counted as invalid and excluded from valid metric denominators |
| Missing Trial record | No fabricated task result | N/A | Counted as unaccounted; it prevents measurement validity |
| Legacy measurable task record without the new field | Existing compatibility default is `true` | Compatibility behavior only | New harness paths always materialize the field explicitly |

The harness normalization rejects `INFRASTRUCTURE_FAILURE`, `CANCELLED` and
`INCONCLUSIVE` as valid measurements and rejects `VerificationState.NOT_RUN`
unless a pre-existing Provider-failure/Task-timeout status is being preserved
for the frozen compatibility contract. A harness-raised timeout/pack
exception is explicitly invalid. This compatibility exception does not turn a
missing Trial into a valid record and does not bypass the final report's
denominator checks.

## 12. Claim Gate

The actual non-compensating logic is:

```text
if measurement_valid is false:
    INVALID_MEASUREMENT / measurement_invalid
elif correctness is false:
    REJECTED / correctness_gate_failed
elif integrity is false:
    REJECTED / integrity_gate_failed
elif regression_non_inferiority is false:
    REJECTED / regression_gate_failed
elif evidence_sufficiency is false:
    INCONCLUSIVE / evidence_insufficient
else:
    SUPPORTED / all_hard_gates_passed
```

The public bounded vocabulary is `SUPPORTED`, `INCONCLUSIVE`, `REJECTED` and
`INVALID_MEASUREMENT`. `efficiency_observed` is carried for explanation only;
it is never used to compensate for correctness, integrity, regression or weak
evidence. `evaluate_paired_claim()` validates count bounds, derives
correctness from candidate passes unless explicitly supplied by the caller,
requires candidate non-inferiority to baseline and applies the minimum valid
pair requirement.

`rebuild_full_report()` still evaluates all legacy per-metric `ClaimRule`
results for diagnostic detail, but its final `positive_claim_eligible` field,
`cv-metrics.json` and Markdown report now use the layered result. A passing
legacy rule therefore cannot bypass the hard gates.

## 13. Denominator Example

The successful Phase 6 repository experiment launches exactly two Trials:

| Count | Value | Meaning |
|---|---:|---|
| Planned/launched | 2 | baseline and candidate for `calculator-add` |
| Accounted/terminal records | 2 | both immutable `trial-record.json` files exist |
| Valid measurements | 2 | both verifiers executed; one passed and one rejected the bug |
| Invalid measurements | 0 | no verifier/infrastructure failure |
| Unaccounted | 0 | `planned - accounted` |
| Task passes | 1 | candidate verifier observed passing tests and required edit |
| Task failures | 1 | baseline verifier observed failing tests/unchanged source |
| Valid measurable denominator | 2 | pass-rate denominator is not silently reduced to one |
| Valid paired comparisons | 1 | same task, fixture and verifier on both variants |

The report therefore exposes `trial.planned=2`, `trial.accounted=2`,
`trial.valid=2`, `trial.invalid=0`, `trial.unaccounted=0` and
`trial.task_passes=1`/`trial.task_failures=1`.

The invalid-measurement test intentionally marks the candidate measurement
invalid while retaining its underlying task status. Its report is
`planned=2`, `accounted=2`, `valid=1`, `invalid=1`, `unaccounted=0`; the final
measurement state is false and the Claim Gate returns
`INVALID_MEASUREMENT`. The invalid row is not silently removed and is not
relabelled as a normal task failure.

## 14. Paired Experiment Example

The existing PicoBench plan creates one comparison block and one pair:

```text
Pair phase6-repository / strategy / calculator-add / repetition 0
    control:   baseline Trial -> left - right -> verifier FAIL
    treatment:  candidate Trial -> left + right -> verifier PASS
```

Both variants use the same materialized fixture, the same sealed test file,
the same required/forbidden path contract and the same `repository_task_v1`
verifier. The `PairResult` is valid only when both selected Attempts are
measurable and their observed variant difference is exactly the declared
`strategy` axis. The fixture's identity sets
`minimum_valid_pairs_per_task=1` for this deterministic acceptance case.

This is a machinery distinction test, not a real-model comparison and not a
production-scale experiment.

## 15. Regression Rejection Evidence

The direct Claim Gate scenario is:

```text
baseline_passes=1
candidate_passes=0
valid_pairs=1
minimum_valid_pairs=1
efficiency_observed=0.90
candidate_correctness_ok=True  # isolates non-inferiority gate
```

The result is:

```text
state  = REJECTED
reason = regression_gate_failed
```

When correctness is not explicitly supplied, a candidate that does not pass
all valid pairs also fails the correctness condition. The test deliberately
sets correctness true to prove the separate regression gate cannot be bought
by a better efficiency observation. There is no weighted scalar in the final
Claim Gate where lower cost/tokens/latency can cancel a correctness or
non-inferiority failure.

## 16. Inconclusive Evidence

The paired scenario with `baseline_passes=0`, `candidate_passes=1`,
`valid_pairs=1`, `minimum_valid_pairs=2` and observed efficiency `0.50`
returns:

```text
state  = INCONCLUSIVE
reason = evidence_insufficient
```

The candidate is not forced into a winner/loser conclusion, and
`positive_claim_eligible` remains false. This is the supported behavior for
weak evidence.

## 17. Trace → Trial → Verifier → Claim Link

The runtime evaluation link is reference-based:

```text
TrialExecution
  -> run_id / turn_id / trace_artifact_ref
  -> AttemptRecord / TrialRecord
  -> verifier_artifact_ref + VerifierResult(id,digest,state)
  -> reducer denominator/pair validity
  -> FullReport ClaimState/reason/gates
```

`RuntimeTrialHost` resolves `run_id` from the durable TraceStore by the
Scheduler-assigned request `turn_id`, including a no-outcome provider-failure
path. The Context and Tool/MCP runtime Packs copy those fields into
`TrialExecution`; the harness copies them into Attempts/Trials and includes
references in the deduplicated artifact set. The verifier and reproducibility
references are carried alongside, not expanded into the Trial record.

The deterministic repository fixture deliberately does not fabricate an
Agent Run for its direct workspace mutation strategy. Its Trial records link
to the verifier result and reproducibility manifest; the actual Runtime/
AgentLoop acceptance path separately proves durable Run/Trace linkage. This
keeps the evidence honest: a direct deterministic fixture is not mislabeled
as an Agent-generated Run.

## 18. Reproducibility Manifest

The representative fixture writes
`reproducibility.json` with schema `pico.picobench.reproducibility.v1`. It
contains bounded:

- PICO source commit from `git rev-parse HEAD` and a workspace-local identity;
- Python implementation/version;
- platform system/release/machine;
- task ID and fixture ID;
- deterministic strategy ID;
- verifier ID and SHA-256 verifier implementation digest;
- relevant test/timeout configuration;
- a canonical digest of the bounded environment identity.

It does not dump ambient environment variables, provider credentials, home
directories, prompts, repository contents or secrets. The manifest is written
in each isolated baseline/candidate Trial and its relative reference is
included in `artifact_refs` and `reproducibility_ref`.

This is a reproducibility identity, not a claim of a hermetic build,
container image lock, real-model determinism or production distributed-run
replay.

## 19. Benchmark Isolation

The repository fixture uses `TrialIsolation` under the experiment output root:

```text
<experiment-root>/.phase6-repository/<attempt-id>/
    workspace/
    reproducibility.json
    verifier-result.json
```

The strategy mutates only the isolated workspace. The Context and Tool/MCP
runtime Packs use their existing isolated workspace/PICO_HOME/session/evidence/
trace roots and set their child environment accordingly. No benchmark
Memory is written to the user's normal Memory directory, and no network or
paid Provider is required by the Phase 6 fixture.

## 20. Failure Matrix

| Mandatory case | Final machine-readable behavior | Direct evidence |
|---|---|---|
| Provider failure | Host returns no `TurnOutcome`, runtime state is `provider_failed`; root Trace has `spine.outcome=provider_failed` and the normalized Provider category | `test_failure_trace_is_linked_without_becoming_failure_authority` |
| Tool validation | `tool.call` retains call/turn identity and `tool.failure_category=tool_validation_failure` | `test_tool_trace_records_failure_category_and_turn_link` |
| Tool execution/timeout | Existing Tool Runtime categories remain bounded as execution failure or timeout | Phase 2 Tool/effect contract plus semantic-convention helper |
| Unknown external effect | Trace may report `tool_effect_unknown`; EffectJournal remains UNKNOWN and no committed claim is made | Phase 2 cancellation/EffectJournal tests and authority boundary |
| Context budget/structure | Existing ContextBudgetError/overflow path retains `context.*` budget/decision/reason fields; root error classification is `context_budget_failure` | Phase 4 context contract and `context.assemble` mainline evidence |
| Recovery block/resume | Fresh-Turn projection emits bounded `recovery.resume` attributes; no automatic replay or Trace-owned recovery truth | Phase 3 recovery projection/trace contract and Phase 4 recovery-budget regression |
| Memory failure/corrupt store | Existing MemoryStore fails closed with bounded diagnostics; Memory trace records decision/diagnostic metadata only | Phase 5 Memory contract and `test_memory_trace_records_bounded_write_recall_and_turn_link` |
| Runtime/internal error | Root `spine.failure_category=runtime_internal_failure` with explicit `error` outcome | `spine_turn_failed` semantic convention and existing Spine tests |
| Verifier task failure | `VerificationState.FAILED` plus `TASK_FAILED`, but measurement remains valid because the verifier ran | `test_sealed_verifier_identity_is_independent_of_task_self_report` and repository pair test |
| Verifier crash | `VerifierState.NOT_RUN` result, `verifier_crashed:<type>` infrastructure error and invalid Trial measurement | `test_verifier_crash_is_not_run_and_is_not_a_task_result` |
| Sealed verifier mutation | Before/after seal mismatch returns `NOT_RUN` and no task verdict | `run_sealed_verifier` seal checks |
| Missing/corrupt evidence | Harness/record validation fails closed; invalid/unaccounted counts prevent a valid positive report | invalid measurement test and ArtifactStore resume checks |
| Oversized prompt/tool/result | Bounded preview/digest/collection/depth policy and artifact byte cap | `test_trace_artifact_is_bounded_redacted_and_reconstructable` |
| Hidden reasoning | Presence/chars/SHA-256 only; raw reasoning absent from output/attrs | `test_semantic_conventions_keep_reasoning_as_metadata_only` |
| Windows locking | Portable blocking/non-blocking locks and Windows-safe atomic artifact path | `test_portable_artifact_and_budget_locks_have_one_cross_platform_path`; final Windows baseline |
| Regression | Hard regression gate returns `REJECTED`, independent of efficiency observation | `test_claim_gate_rejects_regression_even_when_efficiency_improves` |
| Weak evidence | Minimum-pair gate returns `INCONCLUSIVE` | `test_claim_gate_marks_weak_paired_evidence_inconclusive` |
| Invalid measurement | Claim state is `INVALID_MEASUREMENT`; invalid Trial remains in denominator accounting | `test_denominator_accounting_exposes_invalid_trial_and_blocks_claim` |

## 21. Mainline Bypass Audit

The approved runtime boundary remains:

```text
pico run / Runtime assembly
  -> AgentLoop
  -> ContextEngine / existing ContextAssembler
  -> deterministic Provider in acceptance test
  -> ToolRegistry / Tool Runtime
  -> Scheduler lifecycle and terminal state
  -> durable TraceStore Run artifact
  -> RuntimeTrialHost observation / PicoBench Trial fields
  -> deterministic Verifier and result artifact
  -> reducer / Claim Gate / report
```

The Phase 6 mainline test enters `RuntimeTrialHost.build`, which calls the
existing `assemble_runtime` boundary and constructs the real AgentLoop. The
scripted Provider makes one `list_dir` Tool request and then returns a final
answer. The test observes actual `spine.turn`, `session.turn`,
`context.assemble`, `llm.call` and `tool.call` records, then reads the durable
per-Run JSONL after the host closes. The Provider-failure test exercises the
same Scheduler/AgentLoop host boundary and resolves a Trace even though the
result is `None`.

The approved Typer `pico run` route remains covered by the Phase 5 actual CLI
test and remains in the final 643-test explicit baseline. Phase 6 does not
pretend that the direct repository fixture is a CLI invocation; it tests the
existing PicoBench task/verifier path separately and links the actual runtime
Packs through their real Trace references.

No Provider code directly reads local Trace to decide success. No Trace span
is used as a substitute for Session, Memory, EffectJournal, Recovery,
workspace or Verifier truth. No Agent self-report is used as a task verdict.

## 22. Phase 2–5 Regression Evidence

The final authoritative manifest explicitly includes:

```text
tests/test_phase2_tool_runtime_contract.py
tests/test_phase3_recovery_contract.py
tests/test_phase4_context_contract.py
tests/test_phase5_memory_contract.py
tests/test_tracing_api.py
tests/test_no_otel_tracing.py
```

The relevant frozen checks remain green:

- Phase 2 ToolRegistry, timeout, effects, EffectJournal, search and traversal
  guards remain in the final baseline; Trace categories do not replace
  EffectJournal status.
- Phase 3 Recovery tests retain conservative state, no automatic replay,
  fresh-Turn projection, checkpoint reference-only behavior and recovery trace
  attributes.
- Phase 4 Context tests retain provider/configured limits, protected layers,
  keep/drop/compact decisions, structural validation, explicit overflow and
  non-persisted Recovery evidence.
- Phase 5 Memory tests retain single-Store eligibility, scope/lifecycle,
  bounded recall, contamination prevention, null-backend behavior and actual
  `pico run` context integration.

The frozen `TurnOutcome` compatibility regression is specifically covered by
the final run: after the initial failed attempt exposed the issue, the final
targeted command over `tests/test_spine_scheduler.py`,
`tests/test_spine_runner.py` and the Phase 6 contract file passed **57 tests**.

## 23. Files Changed

### Production

- `pico/tracing/store.py` — durable per-Run JSONL, bounded/redacted records,
  artifact size cap, Run lookup and bounded summary.
- `pico/tracing/spans.py` — optional Run/Turn correlation in the existing span
  builder and artifact metadata.
- `pico/tracing/trace.py` — Turn correlation propagation through existing Span
  handles and instrumented artifacts.
- `pico/tracing/semconv.py` — bounded Provider/Tool/Memory/Context/Spine
  evidence, hidden-reasoning metadata-only handling and normalized categories.
- `pico/tracing/__init__.py` — existing TraceStore public export.
- `benchmarks/picobench/artifacts.py` — portable locks and Windows-safe
  atomic/long-path artifact I/O.
- `benchmarks/picobench/budget.py` — portable Provider budget ledger lock and
  Windows-safe high-water persistence.
- `benchmarks/picobench/semantic_campaign.py` — portable semantic campaign
  lock and Windows-safe local persistence.
- `benchmarks/picobench/host.py` — RuntimeTrialHost Run/Turn/trace summary
  observation, including failed/cancelled lookup.
- `benchmarks/picobench/protocol.py` — explicit Trial evidence and measurement
  fields.
- `benchmarks/picobench/records.py` — measurement validity vocabulary and
  persisted Trial/Attempt/Verifier evidence fields.
- `benchmarks/picobench/harness.py` — execution normalization, evidence-field
  propagation, validity-aware resume/pairing and long Windows experiment root.
- `benchmarks/picobench/reducer.py` — valid/invalid/unaccounted denominator
  metrics and validity-aware task/pair reduction.
- `benchmarks/picobench/verifier.py` — verifier seals, repository verifier,
  deterministic result identity and crash/timeout boundary.
- `benchmarks/picobench/claims.py` — bounded ClaimState, paired comparison and
  non-compensating layered Claim Gate.
- `benchmarks/picobench/report.py` — final report eligibility uses layered
  Claim Gate and exposes state/reason/gates.

### Tests

- `tests/test_phase6_evidence_contract.py` — 14 deterministic contracts for
  mainline Trace, durable/bounded payload, Tool/Memory failure attribution,
  Windows locks, verifier independence/crash, repository fixture, denominator,
  paired/regression/inconclusive Claim Gates and invalid measurement.
- `scripts/run_medium_baseline.ps1` — explicit Phase 6 test entry; the script
  remains a bounded manifest rather than unrestricted repository collection.

### Benchmark fixture

- `benchmarks/picobench/fixtures/phase6_repository.py` — isolated calculator
  repository Task/Pair fixture with baseline/candidate strategies, sealed
  deterministic repository verifier and result/manifest artifacts.
- `benchmarks/picobench/reproducibility.py` — bounded
  `pico.picobench.reproducibility.v1` identity capture.

### Docs

No prior Phase 1–5 contract document was rewritten for Phase 6. The existing
dirty-worktree documents `docs/medium-plus-baseline.md` and
`docs/tool-runtime-contract.md` were preserved as carried-forward earlier
phase work. This report is the Phase 6 documentation artifact.

### Baseline

- `scripts/run_medium_baseline.ps1` — explicit manifest now includes
  `tests/test_phase6_evidence_contract.py`.

### Report

- `reports/PHASE_06_LUNA_EXECUTION_REPORT.md` — this report.

Other dirty files from Phase 0–5 were preserved and were not reset or
overwritten. The Phase 6 edits above are intentionally uncommitted in the
shared worktree for Web Reviewer inspection.

## 24. Verification Evidence

Final successful commands:

```text
tests/test_phase6_evidence_contract.py
14 passed in 4.75s

tests/test_spine_scheduler.py tests/test_spine_runner.py
tests/test_phase6_evidence_contract.py
57 passed in 7.45s

powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_medium_baseline.ps1
643 passed in 83.29s (0:01:23)

git diff --check
completed successfully; only Git LF/CRLF normalization warnings were emitted
```

The broad exploratory non-authoritative PicoBench unit selection was also
audited. It produced **412 passed, 16 failed and 1 deselected** before the
final focused additions. The failures were outside the explicit acceptance
manifest and were observed in untouched historical campaign/environment or
timing-sensitive paths: long Windows campaign approval paths, an earlier
Phase 5 context-default expectation, a POSIX-style SandboxConfig test on
Windows and latency/concurrency thresholds. The known symlink privilege case
was deselected because this Windows environment lacks the required symlink
privilege. Those exploratory results are not represented as green claims; the
authoritative bounded manifest is the acceptance result.

The final baseline is the source of truth for Phase 6 acceptance and contains
the actual Phase 2–5 regression tests, existing tracing tests and the new
Phase 6 file. No live LLM, paid provider, network Memory backend or distributed
telemetry service is required for the acceptance path.

## 25. Claims Proven

The current evidence proves that:

- the actual deterministic AgentLoop/Scheduler mainline emits correlated
  Run/Turn/Session/Context/Provider/Tool/terminal evidence;
- the supported local Trace path survives execution as an inspectable per-Run
  JSONL artifact and exposes a bounded summary;
- large payloads are bounded and sensitive-shaped values/hidden reasoning are
  not persisted as raw Trace evidence in the tested paths;
- Provider, Tool, Context, Memory and Recovery boundaries expose normalized,
  machine-readable observations without replacing their authoritative state;
- provider failure can remain a failed runtime result while its Trace remains
  linked, and a Tool validation failure is identifiable without console-text
  parsing;
- Memory write/recall evidence is bounded and correlates with the enclosing
  Run/Turn when invoked;
- Windows PicoBench artifact, budget and semantic campaign locks use one
  portable lock abstraction with blocking/non-blocking mutual exclusion
  semantics preserved;
- a deterministic repository-style Task can distinguish a false Agent
  self-report from an independently observed Verifier failure/pass;
- verifier identity/seals and deterministic workspace/test checks are kept
  outside Agent self-report authority;
- verifier/infrastructure failure can be represented as `NOT_RUN`/invalid
  measurement instead of being silently counted as a normal task failure;
- all planned Trials are represented in denominator accounting as accounted,
  valid, invalid or unaccounted, with task pass/failure counts exposed;
- the existing paired baseline/candidate plan preserves same-task/fixture/
  verifier pairing;
- correctness, integrity, regression/non-inferiority, evidence sufficiency and
  measurement validity are non-compensating Claim Gates;
- positive efficiency observations cannot override a regression, and weak
  evidence can return `INCONCLUSIVE`;
- representative Trials can carry Run/Turn, Trace, Verifier and
  reproducibility references into Attempt/Trial/Report artifacts;
- the final explicit Medium+ baseline remains green at 643 tests after adding
  the Phase 6 evidence contracts;
- the Phase 2–5 frozen contracts remain green in that final authoritative
  baseline.

## 26. Claims NOT Proven

The following claims are intentionally not made:

- real LLM strategy improvement
- statistical superiority at production scale
- benchmark generalization
- zero benchmark overfitting
- perfect failure attribution
- complete secret-redaction security
- production distributed tracing
- automatic self-improvement
- safe autonomous activation

Also not proven are hermetic build reproducibility, arbitrary hidden-secret
encoding detection, real-model quality, semantic retrieval/extraction quality,
production-scale distributed/multi-process stress, universal failure
classification, or any Phase 8 candidate-generation/activation behavior.

**Final status: PASS — READY FOR WEB REVIEW**

**STOP → WAIT FOR WEB REVIEW.**
