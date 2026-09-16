# PICO Medium+ — Final System Audit

Audit mode: AUDIT ONLY  
Audit date: 2026-09-16 (Asia/Shanghai)  
Repository under audit: <repo-root><br>
Final status: **PASS WITH DEBT — READY FOR WEB FINAL ACCEPTANCE**

This report is an independent audit of the current working tree. It does not
grant final acceptance, does not authorize Phase 9, and does not claim a live
production or live-model result. No production source or existing document was
changed during the audit.

## 1. Executive Verdict

The current tree implements one coherent Medium+ runtime/evidence architecture:

* The supported Coding Agent path enters through pico run, assembles one
  Runtime/Spine/AgentLoop path, and uses the final ContextAssembler,
  ToolRegistry, SessionManager, EffectJournal, RecoveryProjector, MemoryStore
  and TraceStore boundaries.
* The authoritative explicit 44-file manifest passes 671 tests on the current
  Windows/Python 3.12 environment.
* The representative Runtime, Recovery, Memory, Evaluation and Evolution
  contract smokes all pass.
* No normal pico run -> Evolver bypass was found; Evolver remains an explicit
  opt-in command.
* The audit found no release-blocking defect in the supported Medium+ path.

The debt is material but bounded: the final implementation is still an
uncommitted, partially untracked working tree; two user-facing documents have
drifted from the final manifest/CLI surface; and non-authoritative AppWorld,
small-real and symlink tests retain Windows/POSIX assumptions. These findings
prevent a claim that the final state is reproducible from HEAD alone or that
all deferred surfaces are portable. They do not invalidate the current
explicit Medium+ baseline.

## 2. Repository Identity

| Field | Observed value |
|---|---|
| Repository path | <repo-root> |
| Branch | feat/durable-execution-phase0 |
| HEAD | 0ae70289b282bdc808e668bd267c7370ffd0e5b8 |
| Python | 3.12.14 |
| Package | pico-harness 0.1.7 |
| CLI version | pico --version -> Pico v0.1.7 |
| Platform | Windows 11, platform release 11, build 10.0.26200 |
| Shell | PowerShell 7.6.5 Core |
| Interpreter used | <repo-root>\.venv\Scripts\python.exe |
| Working-tree status at audit start | 67 entries: 47 modified, 20 untracked |
| Existing phase reports | PHASE_00 through PHASE_08 under reports/ |

The package identity is pico-harness; an earlier probe for the unrelated
distribution name pico-agent failed, while the correct pico-harness metadata
and source version both resolve to 0.1.7.

Tracked modified paths at audit start were:

~~~text
benchmarks/appworld/evolve/eval.py
benchmarks/picobench/artifacts.py
benchmarks/picobench/budget.py
benchmarks/picobench/claims.py
benchmarks/picobench/harness.py
benchmarks/picobench/host.py
benchmarks/picobench/packs/context/runner.py
benchmarks/picobench/packs/tool_mcp/runner.py
benchmarks/picobench/protocol.py
benchmarks/picobench/records.py
benchmarks/picobench/reducer.py
benchmarks/picobench/report.py
benchmarks/picobench/semantic_campaign.py
benchmarks/picobench/verifier.py
pico/agent/context/builder.py
pico/agent/loop/main.py
pico/agent/tools/shell.py
pico/cli/_repl_spine.py
pico/cli/agent_commands.py
pico/config/pico.py
pico/context_engine/__init__.py
pico/context_engine/assembler.py
pico/context_engine/base.py
pico/context_engine/curator.py
pico/context_engine/factory.py
pico/context_engine/history_trimmer.py
pico/context_engine/segments/curator.py
pico/context_engine/segments/memory.py
pico/evolver/__init__.py
pico/evolver/launch/runner.py
pico/evolver/orchestrator/archive.py
pico/evolver/orchestrator/production.py
pico/memory_engine/__init__.py
pico/memory_engine/backend.py
pico/memory_engine/base.py
pico/memory_engine/consolidate/consolidator.py
pico/spine/events.py
pico/spine/runner.py
pico/spine/scheduler.py
pico/tracing/__init__.py
pico/tracing/semconv.py
pico/tracing/spans.py
pico/tracing/store.py
pico/tracing/trace.py
pico/utils/helpers.py
pyproject.toml
tests/test_cli_agent_commands.py
~~~

Untracked paths at audit start were:

~~~text
benchmarks/picobench/fixtures/phase6_repository.py
benchmarks/picobench/reproducibility.py
docs/medium-plus-baseline.md
docs/tool-runtime-contract.md
pico/agent/recovery/
pico/cli/_run_surface.py
pico/context_engine/budget.py
pico/evolver/lineage.py
reports/
scripts/run_medium_baseline.ps1
tests/fixtures/phase7_demo/
tests/test_phase0b1_recovery.py
tests/test_phase1_medium_baseline.py
tests/test_phase2_tool_runtime_contract.py
tests/test_phase3_recovery_contract.py
tests/test_phase4_context_contract.py
tests/test_phase5_memory_contract.py
tests/test_phase6_evidence_contract.py
tests/test_phase7_cli_contract.py
tests/test_phase8_evolution_contract.py
~~~

The modified/untracked set is consistent with the carried Phase 0–8 work; no
additional unexpected audit-time production modification was identified.
However, the accepted runtime directly depends on untracked source such as
pico/agent/recovery/, pico/context_engine/budget.py, pico/cli/_run_surface.py
and pico/evolver/lineage.py. Therefore the audited result is not reproducible
from the reported HEAD alone. A clean commit or equivalent immutable snapshot
is required before claiming freeze-level provenance.

The repository also contains a zero-byte generated lock residue under
.pico/evidence/picobench-semantic/.locks/. It is not a run, trace, evaluation
or evolution artifact and was not used as evidence. It was not cleaned during
the audit.

## 3. Final Shipping Architecture

The actual final architecture is a single Runtime/Spine path with separate
state and evidence domains:

~~~text
pico run
  -> CLI agent command
  -> RuntimeAssembly
  -> TurnRequest
  -> Scheduler / Lane / Worker
  -> AgentTurnRunner
  -> AgentLoop.run_turn
  -> ContextAssembler.assemble
  -> Provider request
  -> ToolRegistry.execute_many
  -> bounded Tool observations and EffectJournal transitions
  -> next AgentLoop iteration
  -> Session persistence / Trace evidence / terminal lifecycle
  -> DeliveryHub / CliOutlet
~~~

The final ContextAssembler is built by
pico/context_engine/factory.py:build_context_engine and is the sole final
provider-facing context owner in the supported path. The older
pico/agent/context/builder.py remains a lower-level compatibility/contributor
object and is not a second final context engine.

## 4. Mainline Call Chain

The inspected current call chain is:

1. pico/cli/commands.py registers the command and
   pico/cli/agent_commands.py:agent handles pico run.
2. The command loads Config/PicoConfig, resolves the provider and paths, and
   calls pico/cli/_runtime_assembly.py:assemble_runtime.
3. Runtime assembly constructs the AgentLoop, shared session/effect/memory
   dependencies and the ContextAssembler through the factory.
4. pico/cli/_repl_spine.py:build_repl constructs an AgentTurnRunner with the
   existing AgentLoop and installs it in Scheduler.
5. Scheduler.submit accepts the TurnRequest, binds it to a per-conversation
   Lane, and owns TurnStarted/TurnFailed/TurnEnded lifecycle.
6. pico/agent/spine_runner.py:AgentTurnRunner.run delegates to
   AgentLoop.run_turn; it does not create another execution engine.
7. AgentLoop._process_message creates the per-turn budget and calls the one
   ContextEngine, which is the ContextAssembler instance.
8. ContextAssembler runs Phase A independent SegmentBuilders, then the
   prefix-dependent Curator, validates tool-pair structure and the final
   provider budget, and returns the exact provider messages.
9. The provider is called. Model-requested tools cross the one
   ToolRegistry.execute_many boundary. Registry callbacks return normalized
   ToolExecution/ToolResult observations to AgentLoop.
10. The AgentLoop either requests another provider iteration or emits a
    terminal result; SessionManager, EffectJournal and TraceStore record their
    own facts, and Scheduler/DeliveryHub deliver the outcome.

No direct model-requested tool execution outside ToolRegistry was found in the
approved path. A repository search found no production import of tests or
Phase 8 test modules, and no Evolver import/reference in the normal
agent_commands/runtime assembly/AgentLoop/Spine path.

## 5. Component Support Matrix

| Component | Status | Current boundary |
|---|---|---|
| pico run CLI | SHIPPING | Supported reviewer-facing entry; real Typer and runtime-spine smoke |
| TurnRequest / Scheduler / Lane | SHIPPING | One admission and lifecycle boundary |
| AgentTurnRunner / AgentLoop | SHIPPING | Main execution and iteration owner |
| ContextAssembler | SHIPPING | One final provider-facing Context owner |
| ToolRegistry | SHIPPING | Sole model-tool resolution/execution boundary |
| EffectJournal | SHIPPING | Append-only effect truth |
| SessionManager | SHIPPING | Conversation/session persistence |
| RecoveryStateStore / RecoveryProjector | SHIPPING | Fresh-Turn durable recovery projection; no process resurrection |
| CheckpointService | SHIPPING, conditional | Best-effort shadow-Git filesystem snapshot |
| Structured MemoryStore | SHIPPING | Evidence-aware local structured memory |
| Optional MemoryBackend | OPTIONAL | Explicit plugin boundary; null backend is supported |
| TraceStore | SHIPPING | Local durable bounded execution evidence |
| PicoBench harness/verifier/Claim Gate | SHIPPING, opt-in evidence | Offline deterministic evaluation path; not an automatic pico run stage |
| Controlled Evolver | OPTIONAL / OPT-IN | Explicit pico evolve command; runtime Candidate Label is the supported label |
| TUI and ui-tui frontend | DEFERRED | Separate Node/RPC surface; not in Medium+ acceptance |
| Channels, Cron and external runtime integrations | DEFERRED/OPTIONAL | Outside the supported pico run baseline |
| AppWorld live subject/model environment | DEFERRED | External environment, not required by the offline baseline |
| Legacy ContextBuilder and old compatibility adapters | LEGACY/SUPPORT | Read/build helpers retained for compatibility, not final ownership |
| Phase contract tests and fixtures | TEST-ONLY | Evidence of deterministic contracts, not production state |

## 6. State-Domain Ownership Matrix

| Domain | Owns/persists | Authoritative vs derived | Writers/readers | Must never be treated as |
|---|---|---|---|---|
| Session | SessionManager owns session keys and persisted append-only-ish message history, including bounded tool observations and assistant/user turns | Authoritative conversation history; recovery/context views are derived | AgentLoop/SessionManager write; Context, CLI and Recovery read | Effect, Trace, Memory or task-success truth |
| Context | ContextAssembler produces the bounded messages/metadata for one provider request | Derived per-Turn provider state; not a durable transcript | SegmentBuilders/Curator contribute; Assembler assembles; AgentLoop reads result | Session history, Memory storage or final task result |
| Memory | MemoryStore owns structured items, schema, provenance, scopes, verification and lifecycle status in local storage | Authoritative reusable-memory item lifecycle; prompt injection is derived | Store policy writes; MemorySegmentBuilder/Personalizer read | Raw Session, ToolResult, Recovery marker or Trace copy |
| Workspace | User repository/files and the configured allowed directory | Authoritative current filesystem bytes for tools; task correctness still requires Verifier where applicable | Authorized tools and explicit host/evaluation paths write; tools/checkpoint/evaluation read | Checkpoint completeness or candidate success by itself |
| Checkpoint | CheckpointService owns shadow-Git snapshots and checkpoint references | Authoritative only for its snapshot receipt; filesystem-oriented and best effort | CheckpointService writes; Recovery reads reference metadata | Python process, provider coroutine, Tool execution or full Session restore |
| EffectJournal | EffectJournal owns effect IDs, immutable invocation identity, state transitions and local-write hash evidence | Authoritative effect lifecycle truth | ToolRegistry writes; Recovery reads; CLI/Trace observe | Trace/CLI prose or Session content |
| RecoveryState | RecoveryStateStore owns the last-turn/checkpoint marker; RecoveryProjector derives candidates/plans from durable sources | Marker is an index; projected plan is derived and read-only | AgentLoop writes marker; Projector/CLI/AgentLoop read | Effect truth, executor, restore operation or automatic replay queue |
| Trace | TraceStore owns per-run JSONL spans/events and bounded out-of-line artifacts | Execution evidence, not runtime authority; writes are best effort | Instrumented boundaries write; Viewer/PicoBench/Evolver read | Task success, effect commitment or recovery source of truth |
| Evaluation artifacts | PicoBench owns Task/Trial/Attempt/Verifier/manifest/report records and denominator/Claim Gate evidence | Verifier and immutable artifacts are authoritative for evaluation; report is rebuilt from records | Harness/Verifier write; reducer/report read | Agent self-report or an efficiency metric alone |
| Evolution artifacts | Evolver owns freeze, lineage, candidate manifest, round journal and activation bundle/state | Durable correlation and approval evidence; activation state does not apply code | Orchestrator writes; human/operator and verifiers read/transition | Serving Runtime state, automatic deployment or production success |

No accidental second authority was found. Trace repeats effect IDs and
statuses only as bounded observations; Recovery re-derives effect candidates
from EffectJournal; Context consumes Memory but does not own Memory items; and
Evaluation/Evolution artifacts do not feed back into normal pico run state.

## 7. Tool Runtime Audit

The current ToolRegistry at pico/agent/tools/registry.py:44 and the EffectJournal
at pico/agent/effects.py:326 enforce the following:

| Condition | Observed behavior |
|---|---|
| Unknown tool | Normalized failed result before tool code and before effect creation |
| Invalid arguments/schema | Cast/recursive schema validation fails before tool code/effect |
| Valid read | READ effect PREPARED -> RUNNING -> COMMITTED when the read completes |
| Valid write_file | LOCAL_WRITE with hash-only pre/post conditions and terminal classification |
| Known read failure/timeout | FAILED, normalized error, no success inference |
| Started cancellation | Conservative UNKNOWN where the execution boundary is uncertain |
| EXECUTE/EXTERNAL/opaque uncertainty | UNKNOWN, never fabricated as committed |
| Registry timeout | Per-tool timeout or the bounded default (300 seconds); blocking interaction is exempt |
| Parallel execution | Only consecutive READ and concurrency-safe calls, capped at four; ordered effects otherwise |

The supported local-write reconciler compares current bytes/existence with
expected pre/post hashes. A post match supports no replay; a pre match can
support an explicit new retry for an unresolved prepared/running/unknown
write; a conflict asks for human judgment.

Effect status is not the same as task completion. CLI and Trace receive
normalized observations and categories, while EffectJournal remains the effect
authority. In particular, a failed or UNKNOWN Tool event is not rendered as a
committed success.

## 8. Recovery Audit

RecoveryProjector at pico/agent/recovery/projector.py:177 is read-only over
Session, workspace observations, EffectJournal and checkpoint references.
RecoveryStateStore at pico/agent/recovery/state.py:99 stores only marker/index
metadata. AgentLoop.prepare_resume at pico/agent/loop/main.py:830 inspects
durable state before Scheduler submission and the next invocation gets a new
TurnRequest/Turn ID.

The final required semantics are implemented as follows:

| Durable fact | Actual plan |
|---|---|
| COMMITTED | NO_REPLAY / use durable observation where available |
| FAILED | No automatic retry; surface a new decision |
| UNKNOWN | No automatic replay; opaque effects require human judgment |
| PREPARED/RUNNING READ | Explicit retry may be allowed because READ has no durable external side effect |
| PREPARED/RUNNING/UNKNOWN LOCAL_WRITE | Explicit retry may be allowed only when current bytes still match the precondition |
| Local-write post match | NO_REPLAY; continue from observed postcondition |
| Local-write conflict | ASK_HUMAN; projector does not write/merge the workspace |
| Missing/corrupt marker/checkpoint/session | Bounded warning/fail-closed projection; no fabricated Session or restore |

The RecoveryState.automatic_replay_effect_ids property is always empty. The
RecoveryPlanner's READ RETRY_ALLOWED value is an explicit plan for a fresh
decision, not an automatic execution queue. The prompt explicitly says that
the previous process/provider/coroutine/Tool is not resumed.

Representative cross-process evidence used fixed identities:

~~~text
writer Session: cli:cross-process
previous Turn marker: turn-runtime-a
prepared effect: effect-cross-process
reader/new Turn: turn-runtime-b
plan: retry_allowed for the unresolved READ
automatic_replay_effect_ids: []
~~~

CheckpointService at pico/agent/loop/checkpoint.py:111 creates a shadow-Git
filesystem snapshot under the configured state root. It is reference-only for
Recovery; checkpoint_restored is false in the recovery trace attributes. It
does not serialize or resurrect Python objects, provider state, Tool
coroutines, arbitrary external effects or the complete conversation.

## 9. Context Audit

ContextAssembler at pico/context_engine/assembler.py:38 is the one final
ContextEngine returned by pico/context_engine/factory.py. It:

* resolves the effective context limit as the conservative minimum of a valid
  configured limit and known provider/model metadata;
* explicitly records configured fallback when provider metadata is unknown;
* reserves completion output and runtime margin and computes an input budget;
* counts tool definitions in the provider estimate;
* protects fixed system material, tool definitions, current user request and
  active recovery evidence;
* groups history and ToolCall/ToolResult pairs atomically;
* compacts bounded live Tool results, preserves recent failures and
  deduplicates only narrow repeated successful observations;
* executes independent SegmentBuilders in Phase A and the Curator in Phase B;
* records bounded ContextDecision KEEP/COMPACT/SUMMARIZE/DROP evidence;
* performs a final structural and numeric provider-budget gate and raises a
  normalized ContextBudgetError when protected content cannot fit.

ContextAssembler.owns_compaction is true, so AgentLoop passes complete
append-only Session candidates and does not run a competing final compaction
authority. Memory enters through MemorySegmentBuilder and is still budgeted by
the Assembler. No later Phase 6–8 code bypasses this final gate.

## 10. Memory Audit

The structured contract is defined in pico/memory_engine/backend.py and
implemented by MemoryStore at
pico/memory_engine/consolidate/consolidator.py:857:

* schema is pico.memory.item.v1;
* scope, kind, verification, status and provenance are explicit;
* repository/project/user identity is part of the selection boundary;
* eligible verified/observed/user-provided claims can be stored;
* uncertain, derived, unclassified, session-local, raw ToolResult and
  recovery-source material is rejected from durable reusable memory;
* deterministic identity deduplicates exact claims;
* conflicting claims create a new version and supersede the active older
  item;
* invalidation/tombstoning preserves lifecycle history instead of silently
  deleting it;
* source-path staleness is excluded from recall;
* lexical ranking, top_k and max_chars are deterministic and bounded;
* corrupt or unavailable storage fails closed with bounded diagnostics.

The current MemorySegmentBuilder at
pico/context_engine/segments/memory.py:26 is the only Context integration
point. It combines optional backend recall and local structured recall into
one bounded # Memory segment; Memory does not directly call the Provider or
inject outside ContextAssembler.

The focused current smoke verifies:

~~~text
repo-a / project-a / user alice
  session-a / turn-1 -> verified repo-local pytest convention -> persisted
fresh session, same repo
  -> bounded recall through MemorySegmentBuilder and ContextAssembler
repo-b or another user
  -> repo/user scoped item excluded
uncertain/derived/tool/recovery candidate
  -> rejected or excluded; no durable reusable hit
~~~

The null backend remains a supported deterministic path. A richer backend is
optional and explicit; a missing selected plugin fails closed rather than
being silently represented as a fake successful backend.

## 11. Trace Audit

TraceStore at pico/tracing/store.py:182 is an evidence sink, not a Runtime
state store. It preserves bounded Run/Turn/Session correlation and writes a
durable per-run JSONL reference:

~~~text
<state_dir>/logs/runs/<trace-id>.jsonl
<state_dir>/logs/audit-artifacts/<kind>/<date>/...
~~~

The current semantic conventions cover spine.turn, session.turn,
context.assemble, llm.call, tool.call, memory.write, memory.recall and
recovery.resume where those boundaries execute. Trace artifacts carry
run_id/traceId, turn_id, session identity, provider/model/call metadata, tool
and memory evidence, terminal outcome and artifact references.

Store-level bounds include a 32 KiB default artifact cap, bounded scalar
previews, collection/depth limits and sensitive-key redaction. Hidden
reasoning is represented as presence/length/digest metadata rather than raw
reasoning content in the tested path. Large payloads become digest/preview
envelopes. Trace writes are best effort; a path/reference is evidence to
inspect, not proof that a remote viewer or external exporter succeeded.

The Phase 6 runtime test observed a dynamic non-empty run ID and asserted:

~~~text
turn_id: phase6-turn
tool_call_id: phase6-list-dir
run_id: dynamic trace-* generated by the real TraceStore
trace_artifact_ref: <temporary trace root>/logs/runs/<run_id>.jsonl
required spans: spine.turn, session.turn, context.assemble, llm.call, tool.call
terminal outcome: completed or completed_with_tool_failure
~~~

The temporary test root is removed by pytest after the test; the asserted
runtime ID/path is not presented as a permanent repository artifact. CLI Run
ID resolution in the Phase 7 contract uses the real TraceStore, not console
text or a second CLI-local trace database.

Trace never upgrades an EffectJournal UNKNOWN to committed, never creates
Recovery truth, and never decides a task claim.

## 12. Evaluation Audit

The current PicoBench boundary keeps the following distinct:

~~~text
Task Result != Measurement Validity != Claim Eligibility
~~~

RuntimeTrialHost at benchmarks/picobench/host.py:48 captures the real
Scheduler/AgentLoop boundary where used. Trial/Attempt records carry runtime
state, Run/Turn/Trace references, Verifier identity/digest, artifact links and
validity. RepositoryTaskVerifier at benchmarks/picobench/verifier.py:134
checks workspace/test observations independently of Agent self-report.
VerifierSeal and run_sealed_verifier return NOT_RUN plus an infrastructure
error for verifier crash, seal mutation or invalid verifier output.

The harness retains planned rows in denominator accounting. A task failure
after a valid verifier run is a valid task failure; a verifier crash/timeout
or missing result is invalid measurement, not a silent ordinary failure and
not a silently removed row.

Claim Gate behavior in benchmarks/picobench/claims.py is non-compensating:
measurement validity, correctness, integrity, regression non-inferiority and
evidence sufficiency are checked as separate gates. Efficiency/cost/latency
cannot compensate for a correctness regression. Weak evidence returns
INCONCLUSIVE; a regression returns REJECTED; infrastructure failure returns
INVALID_MEASUREMENT.

The deterministic evidence chain used by the current tests is:

~~~text
suite: phase6-evidence
pack/fixture: phase6-repository / phase6-calculator-v1
task: calculator-add
verifier: RepositoryTaskVerifier / repository_task_v1
records: attempt-record.json, trial-record.json, verifier-result.json,
         summary.json and REPORT.md in the temporary experiment root
claim states exercised: SUPPORTED, REJECTED and INCONCLUSIVE
invalid path exercised: verifier crash -> NOT_RUN -> invalid measurement
~~~

No claim is made that the temporary deterministic fixture is a production
benchmark result.

## 13. Evolver Audit

The supported controlled chain is implemented as a separate opt-in boundary:

1. load_trace_failure_evidence reads an actual durable TraceStore Run, requires
   a failure/weakness signal and copies only bounded failure attributes/digests;
2. freeze_evolution_run writes an immutable Evolution Run boundary containing
   baseline SHA, runtime label policy, train/sealed task sets, verifier
   identity and Claim Gate identity;
3. CandidateGenerationContext exposes train-side bounded context and omits
   sealed IDs, expected answers, sealed results and verifier implementation
   bytes;
4. the Candidate is bound to source lineage and must pass the canonical
   CandidateManifest/G5 allowlist, digest, fixture and evaluator checks;
5. candidate application uses child commits and isolated worktrees;
6. train scoring is visible to the loop, while SealedTestRunner writes sealed
   results and returns no test numbers to the decision path;
7. independent RepositoryTaskVerifier and layered Claim Gate determine
   evidence eligibility;
8. activation artifacts bind manifest, before/after/rollback snapshots,
   evidence, commit identities and digests;
9. runtime candidates end in pending_human; human_actor is required for the
   pending_human -> ready transition, and the transition method does not apply
   a patch to the serving checkout.

CandidateLabel.runtime is the only supported label. Skill, prompt, policy,
model_profile and route remain explicit unsupported policies rather than being
silently treated as evaluated candidates.

Reward-hacking and isolation audit:

| Risk/control | Result |
|---|---|
| Candidate self-report overriding Verifier | PREVENTED by independent sealed/repository verifier |
| Sealed test IDs entering train/anchor decisions | PREVENTED by explicit leak guard and sealed scorer returning no values |
| Verifier/test mutation | DETECTED/REJECTED by seals, digests, forbidden paths and before/after checks |
| Regression hidden by efficiency gain | PREVENTED by non-compensating Claim Gate |
| Unsupported labels receiving positive evidence | PREVENTED by canonical unsupported policy/freeze guard |
| Candidate patch not matching manifest | DETECTED by target/content/patch digest verification |
| Corrupt rollback/activation bundle | DETECTED by full artifact verification and rollback=before checks |
| Arbitrary opaque external side effects | PARTIALLY COVERED; human gate exists, autonomous reversibility does not |
| Cryptographic sealed isolation | NOT COVERED |
| Population-level generalization/model learning | NOT COVERED |

The current deterministic Phase 8 smoke used:

~~~text
evolution_run_id: phase8-evolution-run-001
candidate_id: v1-c1-p8a1
source run: dynamic trace-* with bounded provider_failed/auth evidence
train: baseline 0/1 -> candidate 1/1
sealed: baseline 0/1 -> candidate 1/1
independent verifier: PASSED
Claim Gate: SUPPORTED
activation state: pending_human
durable paths asserted:
  evolution/evolution_freeze.json
  evolution/lineage/v1-c1-p8a1.json
  activation/<candidate>/candidate_manifest.json
  activation/<candidate>/evidence.json
  activation/<candidate>/{before,after,rollback,activation}.json
  round journal and sealed records/nodes
serving checkout: unchanged
~~~

Those paths are temporary test artifacts and were not activated or retained as
a live production evolution result.

## 14. Normal Runtime / Evolver Isolation

The isolation requirement passes on the inspected supported path:

* pico run imports/uses the runtime assembly, Scheduler and AgentLoop only;
* no normal runtime module imports pico.evolver or starts a candidate run;
* pico evolve is an explicit command group with run/check/status/finalize;
* normal pico run does not generate a Candidate, run a sealed benchmark,
  promote a Candidate, modify the Runtime or activate a Candidate;
* activation is an explicit artifact state transition and requires a human
  actor for the gated promotion;
* no Evolver call was found in the normal CLI/runtime search.

This is a final acceptance boundary and no violation was found.

## 15. Windows / Portability Audit

The supported local environment is Windows 11 + Python 3.12.14 + PowerShell.
The explicit baseline and all five representative contract modules pass there.
The current local implementation uses the portable file-lock abstraction for
the audited PicoBench/Trace critical sections, Windows-safe atomic artifact
operations, bounded path resolution and replacement decoding at relevant
subprocess boundaries. Phase 7 output tests cover the non-ANSI/redirect-safe
CLI path.

Current command/help evidence:

~~~text
pico --version -> Pico v0.1.7
pico evolve --help -> run, check, status, finalize
pico run --help -> message/session/continue/resume/workspace/config/
                  markdown/logs/verbosity
~~~

The following are not general portability claims:

* symlink fixture creation requires a Windows privilege in this token
  (WinError 1314);
* chmod/X_OK expectations from AppWorld tests do not map to this Windows
  environment;
* one small-real setup path compares Git's POSIX slash output to Windows
  Path strings;
* a surface test reads a UTF-8 Chinese YAML document using the Windows default
  GBK codec;
* historical Cron timezone, TUI build, optional channel, POSIX /tmp and
  command-syntax findings remain outside the Medium+ manifest;
* a full live provider, external Memory service, VM or AppWorld environment
  is not part of the supported acceptance path.

Phase 0's old direct fcntl import failure is no longer current: the current
PicoBench package imports successfully, and Phase 6 portable-lock tests are in
the authoritative manifest.

## 16. Historical Test Debt Classification

The current non-authoritative broad command over the 14 test_evolver files
plus tests/test_appworld_precheck.py collected 306 tests and produced:

~~~text
285 passed, 21 failed in 101.74s
~~~

The 21 failures are reproducible in this Windows token and are outside the
explicit 671-test manifest:

| Class | Test/path | Failure reason and current reproducibility | Medium+ / resume effect | Recommended action |
|---|---|---|---|---|
| C. DEFERRED LEGACY / ENVIRONMENT DEBT | Six symlink cases in tests/test_evolver_activation_artifacts.py: creation/verification/summary root, candidate directory and payload cases | Fixture creation itself raises WinError 1314 before the system under test runs | No effect on 671-test path; no effect on fresh-Turn recovery semantics; limits broad symlink portability evidence | Run with Windows Developer Mode/appropriate privilege or a POSIX security CI job |
| C. DEFERRED LEGACY / ENVIRONMENT DEBT | Eight symlink-dependent cases in tests/test_evolver_candidate_manifest.py, including path guard, parent-tree, G5 and child-commit cases | The shared fixture cannot create its symlink, so the guard is not reached | No effect on Medium+; no effect on resume claim; broad candidate containment remains only partially exercised on this token | Platform-aware symlink fixtures and a privileged/POSIX CI lane |
| C. DEFERRED LEGACY / ENVIRONMENT DEBT | tests/test_evolver_git_ops.py::TestCommitFilesAsChild::test_rejects_symlink_from_parent_tree_before_worktree_write | Symlink fixture creation raises WinError 1314 | No effect on Medium+ or resume | Re-run in a symlink-capable environment |
| C. DEFERRED LEGACY / ENVIRONMENT DEBT | tests/test_evolver_launch.py::TestAppWorldEntry::test_non_executable_appworld_binary_refuses_at_build and tests/test_appworld_precheck.py::{test_non_executable_runtime_fails_gate_zero,test_broken_appworld_import_fails_gate_zero} | POSIX chmod/X_OK and synthetic /bin/sh subprocess assumptions produce Windows os.access/OSError behavior | No effect on Medium+; no effect on recovery; affects only external AppWorld precheck portability | Use platform-native executable fixtures or classify the external bench as POSIX/controlled-environment only |
| C/E. DEFERRED PLATFORM DEBT / OBSOLETE EXPECTATION | tests/test_evolver_small_real_bench.py::test_setup_script_materializes_a_deterministic_subject | Git ls-tree emits slash-separated paths while the test builds Windows backslash strings; reproducible assertion mismatch | No effect on Medium+; no effect on resume semantics; limits small-real portability | Canonicalize both sides to POSIX Git paths |
| C. DEFERRED LEGACY / ENVIRONMENT DEBT | tests/test_evolver_small_real_bench.py::test_setup_script_rerun_needs_an_explicit_mode | Production _matches_template uses the same uncanonicalized Git-vs-Path comparison, so --recreate refuses the otherwise exact Windows checkout | No effect on Medium+; no effect on fresh-Turn recovery; affects deferred small-real reproducibility | Normalize Git paths in the future small-real support path |
| E. OBSOLETE EXPECTATION | tests/test_evolver_surface_contract.py::test_shipped_example_requires_sealed_external_output | UTF-8 docs/examples/evolve_appworld.yaml is read using the Windows default codec and raises UnicodeDecodeError before assertions | No effect on Medium+; no effect on resume; documentation content itself exists | Make the test's encoding explicit and retain UTF-8 docs |

The count is 15 symlink fixture failures plus six platform/encoding/path
failures. No failure was silently skipped or repaired.

Historical findings from earlier reports were reclassified as follows:

| Historical observation | Current classification |
|---|---|
| Phase 0 direct PicoBench fcntl import failure | Resolved by Phase 6 portable_lock; current import and baseline pass; not a remaining failure |
| Phase 0 three Cron timezone failures | C, deferred environment/product surface; not in Medium+ |
| Phase 0 TUI dist/esbuild/handshake failures | C, deferred frontend/build surface; not in Medium+ |
| Phase 0 optional channel SDK failures | C, optional dependency surface; not in Medium+ |
| Phase 0 ten token-wise pricing/cache mismatches | E/B for any token-wise claim until its changed contract is deliberately resolved; exact old selection was not the final manifest and is not part of the Medium+ claim |
| Phase 0 33 full-collection errors and POSIX /tmp/subagent/platform cases | C, broad repository/platform debt; explicit positive manifest is the acceptance boundary |
| Phase 6 exploratory 412 passed/16 failed/1 deselected PicoBench selection | The old exact command was not preserved. Reported long Windows approval/POSIX fixture cases are C; latency/concurrency thresholds are D; the earlier context-default expectation is E/superseded. They were not converted into baseline passes |
| Phase 8 broad Evolver failures | Revisited by the current 306-test command above; the 21 concrete failures are classified in the first table |

No A. RELEASE BLOCKER was found. B. MEDIUM+ CLAIM RISK applies to provenance
and any claim that extends beyond the explicit offline contract, not to a
failing test in the supported manifest.

## 17. Authoritative Baseline

Command:

~~~powershell
.\scripts\run_medium_baseline.ps1
~~~

Current result:

~~~text
exit 0
671 passed in 142.46s (0:02:22)
~~~

The script contains an explicit array of 44 test files and invokes pytest only
on that array. It is not unrestricted repository collection. The included
Phase 1–8 contract tests use scripted/fake providers and deterministic local
fixtures; no paid API, live model, network service or hidden external Memory
backend is required. The baseline explicitly sets the null backend in the
deterministic paths where Myna is absent. Phase 6/8 evidence code is tested
offline, but normal pico run does not automatically run it.

Compileall over pico, benchmarks/picobench and the representative contract
files completed without output or error. git diff --check reported no
whitespace errors; Git emitted only the existing LF-to-CRLF normalization
warnings.

## 18. Representative Runtime Smoke

Current test: tests/test_phase1_medium_baseline.py -> 2 passed in 3.10s.

This smoke enters the actual Typer pico run -m command and the actual
assemble_runtime -> build_repl -> Scheduler -> AgentTurnRunner -> AgentLoop
chain. Only the provider is scripted. It performs:

~~~text
conversation: cli:phase1-baseline
workspace input: phase1-sentinel.txt
provider ToolCall: phase1-read-1 -> read_file
tool observation: PHASE1_SENTINEL
terminal reply: PHASE1_RUNTIME_OK
provider calls: 2
EffectJournal: read_file terminal state committed
Session: user request and Tool observation asserted
~~~

The complementary Phase 6 real-mainline trace test (included in the
representative evidence set) asserts a dynamic real run ID, fixed turn_id
phase6-turn, tool call phase6-list-dir, a durable per-run JSONL reference and
the required spine/session/context/llm/tool span set. This is evidence from
the real runtime boundaries, not a test-only replacement Runtime.

## 19. Representative Recovery Smoke

Current test: tests/test_phase3_recovery_contract.py -> 14 passed in 4.11s.

The cross-process scenario persists Session cli:cross-process, an interrupted
Turn turn-runtime-a and PREPARED effect-cross-process, terminates the writer
process, then constructs a new process/projector and a new Turn
turn-runtime-b. The reader observes retry_allowed for the READ candidate while
automatic_replay_effect_ids remains empty. The marker/effect/session files are
temporary durable artifacts created by the test; no prior Python process,
provider, Tool object or coroutine is passed to the reader.

The test suite also covers COMMITTED/FAILED/UNKNOWN, local-write pre/post/hash
conflict outcomes, missing/corrupt inputs, checkpoint reference-only behavior
and bounded recovery trace attributes.

## 20. Representative Memory Smoke

Current test: tests/test_phase5_memory_contract.py -> 20 passed in 8.04s.

The smoke uses the real MemoryStore with repo-a/project-a/user alice, writes a
verified repo-local pytest convention with session-a/turn-1 provenance, then
recalls it from a fresh session in the same repository through
MemorySegmentBuilder and ContextAssembler. A different repository/user is
excluded. Unverified, derived, session-local, unclassified, raw ToolResult
and recovery inputs do not become durable reusable memory. Duplicate,
supersession, invalidation/tombstone, stale-source and corrupt-storage
diagnostics are also asserted. Temporary memory_items.jsonl data is removed
with the test root.

## 21. Representative Evaluation Smoke

Current test: tests/test_phase6_evidence_contract.py -> 14 passed in 6.89s.

The deterministic repository pack executes the actual RuntimeTrialHost path
where applicable and the independent repository verifier boundary:

~~~text
Task: calculator-add
Pack: phase6-repository
Fixture: phase6-calculator-v1
Verifier: RepositoryTaskVerifier / repository_task_v1
Artifacts: attempt/trial/verifier result, summary and Markdown report
Positive Claim Gate: SUPPORTED
Regression path: REJECTED
Weak evidence path: INCONCLUSIVE
Verifier crash path: NOT_RUN / INVALID_MEASUREMENT
~~~

The tests assert that Agent self-report cannot change the verifier result,
planned denominator rows are not silently removed, paired baseline/candidate
identity is retained, and efficiency cannot compensate for correctness
regression.

## 22. Representative Evolution Smoke

Current test: tests/test_phase8_evolution_contract.py -> 18 passed in 58.82s.

The positive path crosses actual TraceStore failure evidence, evolution freeze,
candidate generation binding, CandidateManifest/G5, child commit/worktree
evaluation, train/sealed split, independent RepositoryTaskVerifier,
layered Claim Gate and activation artifact creation. It records:

~~~text
Evolution Run: phase8-evolution-run-001
Candidate: v1-c1-p8a1
Source failure: dynamic Trace run with provider_failed/auth signal
Train: 0/1 baseline -> 1/1 candidate
Sealed: 0/1 baseline -> 1/1 candidate
Verifier: independent PASSED
Claim: SUPPORTED
Activation: pending_human
Serving checkout: unchanged
~~~

The negative tests reject forbidden verifier/evaluator/test/hidden-answer
paths, sealed regression and self-reported fixes. The inconclusive test
returns INCONCLUSIVE when evidence is insufficient. The test-created durable
artifact names include evolution/evolution_freeze.json,
evolution/lineage/v1-c1-p8a1.json and the activation bundle
candidate_manifest.json, evidence.json, before.json, after.json,
rollback.json and activation.json, plus round/sealed records. No candidate was
activated and no serving Runtime was modified.

## 23. Documentation Drift

The following are exact current drift locations; they were not edited:

1. docs/medium-plus-baseline.md describes itself as a Phase 1 contract and
   says PicoBench and Evolver are outside the baseline. Its inclusion section
   mentions Phase 2, Phase 3 and Phase 5, but omits the final Phase 4, Phase 6,
   Phase 7 and Phase 8 contract files even though
   scripts/run_medium_baseline.ps1 now includes all of them. The statement is
   still reasonable if read as “pico run does not automatically execute
   evidence/evolution,” but it is stale as a description of the final test
   manifest.
2. The same document says the parent
   <parent-root>\.pico-baseline-venv is preferred. The current script first
   prefers <repo-root>\.venv and only then the parent
   environment. The actual audit used the repository .venv.
3. pico/cli/tui_commands.py:828 tells users to use pico run --legacy-repl when
   Node is missing. Current pico run --help has no --legacy-repl option. This is
   a stale TUI error-path instruction outside the Medium+ acceptance surface.
4. The historical PHASE_00–PHASE_08 reports intentionally preserve the status
   and baseline count at the time each phase stopped. Their older counts
   (565/584/598/609/629/643/653) must not be read as the current final count;
   the current manifest result is 671. This is historical versioning, not a
   reason to rewrite those audit records.

No unsafe autonomous-self-improvement, process-resurrection, exactly-once,
unlimited-context or perfect-memory statement was found in the current
README/docs search. The README's controlled-improvement wording remains
bounded by explicit activation/rollback language.

## 24. Legacy / Deferred Paths

| Path | Classification | Audit finding |
|---|---|---|
| pico/agent/context/builder.py | KEEP — compatibility/support; RISK if misread | Still constructs low-level identity/bootstrap/message helpers and owns shared Store references, but ContextAssembler is the final window owner |
| pico/context_engine/factory.py legacy engine config field | KEEP — compatibility/support | legacy/curator/default dispatch is ignored because one ContextAssembler is returned |
| MemoryConsolidator/legacy Markdown history in pico/memory_engine | KEEP — compatibility/support | Existing long-term Markdown/consolidation support remains separate from structured Memory item authority |
| pico/tui_rpc and ui-tui | DEFER — outside Medium+ | Separate UI/frontend/build path; no normal pico run bypass found |
| Channels/Cron/Gateway integrations | DEFER — outside Medium+ | Optional surfaces retain their own adapters but use the shared runtime where wired |
| AppWorld legacy trial/trajectory and small-real support paths | DEFER — external/legacy evidence | Useful benchmark compatibility; Windows portability is not part of the final baseline |
| Unsupported Candidate Labels | KEEP — explicit policy | Honest unsupported configurations; they cannot be frozen as positive runtime evidence |
| Phase 0B.1 recovery contract test | TEST-ONLY / KEEP | It pins pure recovery behavior; production recovery package is now wired separately |
| .pico/evidence/.../.locks zero-byte file | REMOVE LATER — generated residue | Not source or evidence; deliberately not cleaned in audit |

No legacy path was found to be the normal shipping execution authority. The
main risk is reader confusion if a compatibility builder or deferred UI path is
mistaken for the final Medium+ owner.

## 25. Security Boundary

Actual guarantees observed:

* Registry schema validation, unknown-tool rejection and normalized errors
  precede Tool code/effect creation.
* Effect uncertainty is explicit and UNKNOWN is not converted to success or
  auto-replay.
* Filesystem/tools use allowed-directory and path traversal guards.
* Candidate manifests bind target paths, before/after content digests, fixture,
  evaluator and activation policy; unsupported labels are fail-closed.
* Sealed verifier checks protect the deterministic test definition and detect
  mutation/crash/invalid output.
* Train and sealed task sets are separated by construction in the tested path.
* Trace payloads are bounded/redacted and hidden reasoning is not persisted raw
  in the tested conventions.
* Activation artifacts are fully verified and human-gated.

Not proven, and not to be described as proven:

* strong OS/VM sandboxing (DirectExecutor is explicitly host execution);
* cryptographic sealed-evaluation isolation;
* multi-user isolation beyond the tested Memory identity scopes;
* remote execution or remote-side-effect security;
* production supply-chain/security certification;
* universal secret detection in arbitrary payload encodings;
* distributed locking/tracing or adversarial multi-process stress;
* complete reversibility of arbitrary external effects.

## 26. SAFE CLAIMS

The following claims are supported with the indicated evidence:

* The supported pico run path crosses the real CLI/runtime assembly,
  Scheduler, AgentTurnRunner, AgentLoop, ContextAssembler, provider,
  ToolRegistry, Session and EffectJournal boundaries. Evidence: code paths and
  tests/test_phase1_medium_baseline.py.
* Tool inputs are schema-validated; unknown tools and invalid arguments fail
  before Tool code/effect creation. Evidence: ToolRegistry source and Phase 2
  contract tests in the 671 baseline.
* Effect lifecycle states and conservative uncertainty are explicit; UNKNOWN
  is not committed or automatically replayed. Evidence: EffectJournal,
  RecoveryProjector/Planner source and Phase 2/3/7 tests.
* Resume inspects durable state and creates a new Turn rather than resurrecting
  a previous process/coroutine. Evidence: Recovery source and cross-process
  Phase 3 smoke.
* Context assembly is bounded, protected and explainable with a final provider
  budget/structure gate. Evidence: ContextAssembler source, Phase 4 tests and
  baseline.
* Structured Memory has explicit schema/provenance/scope/verification/lifecycle
  and bounded same-repository recall through ContextAssembler. Evidence:
  MemoryStore/MemorySegmentBuilder source and Phase 5 smoke.
* Trace provides a durable bounded per-run JSONL evidence reference with
  Run/Turn/span correlation. Evidence: TraceStore source and Phase 6/7 tests.
* Evaluation separates Task Result, Measurement Validity and Claim Eligibility;
  independent Verifier output can reject self-report and invalid measurements
  do not silently disappear. Evidence: PicoBench verifier/reducer/Claim Gate
  source and Phase 6 smoke.
* The runtime Candidate Label is bound to allowlisted paths, isolated
  train/sealed evaluation, independent verification and human-gated activation.
  Evidence: Phase 8 lineage/manifest/activation source and 18-test smoke.
* Evolver is opt-in and normal pico run does not generate, promote or activate
  candidates. Evidence: CLI help, source search and isolation audit.
* The current explicit Windows/Python 3.12 Medium+ manifest passes 671 tests.
  Evidence: scripts/run_medium_baseline.ps1 execution.

## 27. UNSAFE CLAIMS

Do not say that this audit proves any of the following:

* the project is production-grade in every surface or generally
  cross-platform;
* execution is exactly-once or every external side effect is replay-safe;
* crash recovery is perfect, lossless or a full process resurrection;
* checkpointing restores Python/provider/Tool process state;
* Context has zero information loss, unlimited capacity or exact token counts
  for every provider;
* Memory is perfect, semantically complete, always correct or production-scale;
* the system autonomously self-improves in production;
* all Candidate Labels are supported;
* the deterministic fixture proves statistically significant global improvement
  or cross-model generalization;
* activation or rollback is automatic in production;
* sealed evaluation is cryptographically isolated;
* DirectExecutor is a strong OS sandbox;
* TUI, channels, Cron, live AppWorld, live providers or external Memory are
  fully accepted by the Medium+ baseline;
* the final state is reproducible from HEAD without first freezing the dirty
  working tree.

## 28. Technical Debt Register

| ID | Issue | Severity | Affected area | Blocks Medium+? | Blocks resume claim? | Recommended future action |
|---|---|---|---|---|---|---|
| AUD-01 | Final Phase 0–8 implementation and accepted runtime dependencies are uncommitted/partially untracked | High provenance debt | Repository freeze/reproducibility | No for current tree; yes for HEAD-only reproduction | Partial: behavior is tested, clean-checkout reproduction is not | Freeze/commit the exact audited tree and retain the manifest/interpreter identity |
| AUD-02 | Windows token cannot create symlink fixtures; POSIX chmod/X_OK assumptions fail in deferred AppWorld/small-real tests | Medium portability debt | Evolver/AppWorld/path containment | No | No for fresh-Turn semantics; limits broad Evolver portability claim | Add platform-native fixtures and run a privileged Windows or POSIX security lane |
| AUD-03 | docs/medium-plus-baseline.md and TUI --legacy-repl message drift from script/help | Medium documentation debt | Reviewer instructions/TUI error path | No | No | Reconcile final manifest, interpreter priority and valid CLI remediation |
| AUD-04 | No retained real live-provider/AppWorld/Evolver run artifact exists in this checkout | Medium evidence-scope debt | Live/production evolution claim | No for offline deterministic contract | Yes for any claim of real production evolution/resume provenance | Run only an explicitly approved external evaluation and retain Trace/Trial/Verifier/activation artifacts |
| AUD-05 | TUI, channels, Cron and external runtime surfaces remain deferred | Medium scope debt | Product surfaces outside pico run | No | No for core resume; yes for those surfaces | Give each surface a separate supported environment and acceptance manifest |
| AUD-06 | Small-real --recreate path uses uncanonicalized Git/Path strings; UTF-8 docs test uses locale default | Low-to-medium test/platform debt | Deferred benchmark setup/docs test | No | No | Normalize Git paths and specify UTF-8 in future support changes |
| AUD-07 | Zero-byte ignored PicoBench lock residue remains under .pico | Low hygiene debt | Generated artifacts | No | No | Clean generated residue during an approved workspace hygiene pass |

## 29. Final Architecture Consistency Verdict

### A. One coherent PICO Medium+ architecture

**PASS WITH DEBT** — The real call chain converges on one Scheduler/
AgentLoop/ContextAssembler/ToolRegistry path and the 671 manifest passes.
Dirty/untracked provenance prevents a clean freeze claim.

### B. Normal Coding Agent mainline uses intended Runtime contracts

**PASS** — Source search and the Phase 1/6/7 real-mainline tests show the
normal path uses RuntimeAssembly, Scheduler, AgentTurnRunner, AgentLoop,
ContextAssembler, ToolRegistry, Session, EffectJournal and Trace.

### C. State domains remain separated

**PASS WITH DEBT** — The ownership matrix and source boundaries are coherent.
Trace/Recovery/Evaluation intentionally duplicate bounded references for
evidence, and legacy compatibility objects remain present, so documentation
must keep the authority distinctions visible.

### D. Recovery remains conservative

**PASS** — The cross-process Phase 3 smoke creates turn-runtime-b after
turn-runtime-a, retains UNKNOWN protection and has an empty automatic replay
set. Checkpoint remains reference-only.

### E. Context remains bounded

**PASS** — Phase 4 budget/structure/protected-decision tests and the final
Assembler budget gate are included in the green 671 manifest.

### F. Memory remains scoped and evidence-aware

**PASS** — Phase 5's 20-test smoke verifies schema, provenance, scope,
verification, lifecycle, bounded recall and Context-only integration.

### G. Evaluation remains independent from Agent self-report

**PASS** — Sealed RepositoryTaskVerifier, validity-aware denominator accounting
and non-compensating Claim Gate tests pass in the current evidence path.

### H. Controlled Self-Evolution is actually controlled

**PASS WITH DEBT** — Phase 8 crosses freeze, lineage, allowlist, isolated
train/sealed evaluation, independent verification and pending_human activation.
No live production run artifact or cryptographic isolation proof exists.

### I. Evolver remains opt-in

**PASS** — pico evolve is a separate explicit command group; normal pico run
has no Evolver invocation or automatic candidate/activation stage.

### J. Repository is safe to freeze for resume/interview use

**PASS WITH DEBT** — The tested behavior is suitable for a bounded technical
discussion, but the current tree must first be frozen/committed and claims
must retain the explicit limitations above.

## 30. Files / Artifacts Inspected

Key inspected implementation files:

~~~text
<repo-root>\pico\cli\agent_commands.py
<repo-root>\pico\cli\_runtime_assembly.py
<repo-root>\pico\cli\_repl_spine.py
<repo-root>\pico\spine\scheduler.py
<repo-root>\pico\agent\spine_runner.py
<repo-root>\pico\agent\loop\main.py
<repo-root>\pico\agent\tools\registry.py
<repo-root>\pico\agent\effects.py
<repo-root>\pico\agent\recovery\projector.py
<repo-root>\pico\agent\recovery\planner.py
<repo-root>\pico\agent\recovery\state.py
<repo-root>\pico\agent\loop\checkpoint.py
<repo-root>\pico\session\manager.py
<repo-root>\pico\context_engine\factory.py
<repo-root>\pico\context_engine\assembler.py
<repo-root>\pico\context_engine\budget.py
<repo-root>\pico\memory_engine\backend.py
<repo-root>\pico\memory_engine\consolidate\consolidator.py
<repo-root>\pico\context_engine\segments\memory.py
<repo-root>\pico\tracing\store.py
<repo-root>\pico\tracing\semconv.py
<repo-root>\benchmarks\picobench\host.py
<repo-root>\benchmarks\picobench\verifier.py
<repo-root>\benchmarks\picobench\claims.py
<repo-root>\benchmarks\picobench\harness.py
<repo-root>\benchmarks\picobench\reproducibility.py
<repo-root>\pico\evolver\candidate_manifest.py
<repo-root>\pico\evolver\lineage.py
<repo-root>\pico\evolver\activation\artifacts.py
<repo-root>\pico\evolver\orchestrator\production.py
<repo-root>\pico\evolver\orchestrator\sealed\runner.py
~~~

Key inspected tests/docs/reports:

~~~text
<repo-root>\scripts\run_medium_baseline.ps1
<repo-root>\tests\test_phase1_medium_baseline.py
<repo-root>\tests\test_phase3_recovery_contract.py
<repo-root>\tests\test_phase5_memory_contract.py
<repo-root>\tests\test_phase6_evidence_contract.py
<repo-root>\tests\test_phase8_evolution_contract.py
<repo-root>\docs\medium-plus-baseline.md
<repo-root>\docs\tool-runtime-contract.md
<repo-root>\README.md
<repo-root>\reports\PHASE_00_LUNA_EXECUTION_REPORT.md
<repo-root>\reports\PHASE_01_LUNA_EXECUTION_REPORT.md
<repo-root>\reports\PHASE_02_LUNA_EXECUTION_REPORT.md
<repo-root>\reports\PHASE_03_LUNA_EXECUTION_REPORT.md
<repo-root>\reports\PHASE_04_LUNA_EXECUTION_REPORT.md
<repo-root>\reports\PHASE_05_LUNA_EXECUTION_REPORT.md
<repo-root>\reports\PHASE_06_LUNA_EXECUTION_REPORT.md
<repo-root>\reports\PHASE_07_LUNA_EXECUTION_REPORT.md
<repo-root>\reports\PHASE_08_LUNA_EXECUTION_REPORT.md
<repo-root>\.pico\evidence\picobench-semantic\.locks\
~~~

No retained fresh .pico run_meta.json, evolution_freeze.json, activation
bundle, subject checkout or live benchmark output was found in the repository.
The only observed .pico content was the zero-byte lock residue described above.

## 31. Commands Executed

The principal audit commands and outcomes were:

~~~text
git branch --show-current; git rev-parse HEAD; git status --short
  -> branch/HEAD/worktree inventory recorded above

.venv\Scripts\python.exe -c "<package/version/platform identity>"
  -> pico 0.1.7, pico-harness 0.1.7, Python 3.12.14, Windows 11

.venv\Scripts\python.exe -m pico --version
.venv\Scripts\python.exe -m pico evolve --help
.venv\Scripts\python.exe -m pico run --help
  -> all command/help probes succeeded

rg production source searches for test imports, Phase 8 imports, direct
Tool execution and normal-path Evolver references
  -> no test import, no normal-path Evolver reference, one Registry execution boundary

.venv\Scripts\python.exe -m pytest -q tests/test_phase1_medium_baseline.py
  -> 2 passed in 3.10s

.venv\Scripts\python.exe -m pytest -q tests/test_phase3_recovery_contract.py
  -> 14 passed in 4.11s

.venv\Scripts\python.exe -m pytest -q tests/test_phase5_memory_contract.py
  -> 20 passed in 8.04s

.venv\Scripts\python.exe -m pytest -q tests/test_phase6_evidence_contract.py
  -> 14 passed in 6.89s

.venv\Scripts\python.exe -m pytest -q tests/test_phase8_evolution_contract.py
  -> 18 passed in 58.82s

.\scripts\run_medium_baseline.ps1
  -> exit 0; 671 passed in 142.46s

.venv\Scripts\python.exe -m pytest -q <14 test_evolver files> tests/test_appworld_precheck.py
  -> 285 passed, 21 failed in 101.74s; non-authoritative debt classification above

.venv\Scripts\python.exe -m pytest --collect-only -q <19 test_picobench files>
  -> 319 tests collected; this was not an acceptance result

.venv\Scripts\python.exe -c "import benchmarks.picobench"
  -> picobench_import=ok; old fcntl import failure is resolved

.venv\Scripts\python.exe -m compileall -q pico benchmarks/picobench <representative tests>
  -> completed without error

git diff --check
  -> no whitespace errors; LF/CRLF normalization warnings only
~~~

An attempted non-authoritative full PicoBench run lost its host stdout before
producing a result; no Python test process remained on the subsequent process
probe, so it is deliberately not reported as pass or fail evidence.

## 32. Claims NOT Proven

This audit does not prove:

* a clean-checkout reproduction from HEAD without freezing the dirty tree;
* a full repository test-suite pass;
* TUI frontend/RPC completion, channel, Cron or external runtime acceptance;
* live provider/LLM quality, cost, fallback or deterministic replay;
* production-scale Memory quality, semantic extraction or vector-search quality;
* exactly-once execution, process resurrection, zero-loss recovery or
  automatic arbitrary-effect replay;
* successful checkpoint restore of process state or arbitrary external effects;
* strong OS/VM sandboxing or cryptographic sealed isolation;
* multi-user/remote/supply-chain security certification;
* a real local AppWorld run, live Evolver run, production activation, rollback
  or canary deployment;
* statistically significant or general self-improvement;
* support for every Candidate Label;
* a universal cross-platform claim beyond the tested Windows/Python 3.12
  explicit manifest.

## 33. Recommendation to Web Final Reviewer

Web Final Reviewer should decide whether to accept the bounded current result
with the debt register above, or require a freeze/commit and documentation
reconciliation before calling the repository interview-ready. The smallest
provenance action is to preserve the exact audited tree, interpreter identity,
manifest and this report as one immutable review snapshot. The deferred
Windows symlink/AppWorld/small-real, TUI, Cron, channel and live-evaluation
surfaces should be handled only in their own approved environment and scope.

No production fix, cleanup, activation, Phase 9 work, resume redesign or
interview rewrite was performed. STOP and wait for Web final acceptance.

**PASS WITH DEBT — READY FOR WEB FINAL ACCEPTANCE**
