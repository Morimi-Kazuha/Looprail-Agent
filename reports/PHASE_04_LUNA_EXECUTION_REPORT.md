# PICO Medium+
## Luna MAX Execution Report — Phase 4

**Report date:** 2026-09-15  
**Repository:** D:\Agent Learning\Pico Agent  
**Working-tree HEAD:** 0ae70289b282bdc808e668bd267c7370ffd0e5b8  
**Approved demo surface:** pico run  
**Authoritative baseline:** .\scripts\run_medium_baseline.ps1

## 1. Status

**PASS — READY FOR WEB REVIEW**

Phase 4 hardens the existing shipping Context Engine. The Runtime now resolves
a provider-aware input budget, reserves completion output and a configurable
runtime margin, applies layered retention and bounded in-flight Tool-result
compaction, preserves ToolCall/ToolResult structure, records explainable
decisions, and emits Context trace spans. Phase 3 recovery remains a
read-only, fresh-Turn projection; its active evidence is inserted before
Context assembly and is protected by the same budget gate.

The final supported baseline passed **609 tests in 70.52s**. The previously
accepted Phase 3 baseline was 598 tests; Phase 4 adds 11 explicit Context
contract tests. After this report, Phase 4 stops and waits for Web Review.
Structured Memory, Eval/PicoBench redesign, CLI/TUI/channel expansion and
Evolver work were not started.

## 2. Task and Scope

The scope is the existing per-request Context path, not a new Memory system:

~~~
Context != Session != Memory
~~~

Session remains the append-oriented durable conversation/task history. Context
is the bounded provider-facing message list for one Turn. Memory remains the
existing cross-Turn/cross-Session subsystem and is only consumed through the
already shipping Context segments. No new vector store, retrieval platform,
Memory schema, Memory invalidation rule, or Memory write policy was added.

The implementation is limited to Context assembly, budget and output
reservation, retention/compaction, Tool observation handling, recovery
evidence retention, explainability, tracing, tests, and the baseline manifest.

## 3. Mandatory Pre-Implementation Audit

The actual shipping layout was inspected before source changes. The protocol's
older directory names are not the active implementation; the relevant paths
are pico/context_engine/, pico/agent/context/, pico/agent/loop/, pico/session/,
pico/memory_engine/, pico/providers/, and pico/cli/_runtime_assembly.py.

The audited mainline was:

~~~
pico run
  -> existing CLI/runtime assembly
  -> TurnRequest
  -> AgentLoop._process_message
  -> ContextAssembler
       -> Phase A identity/bootstrap/memory/skills
       -> Phase B Curator + HistoryTrimmer
       -> [system, *history, current user]
  -> AgentLoop._run_agent_loop
  -> Provider.chat_with_retry / chat_stream
~~~

Before Phase 4, the audit found that ContextAssembler was already the
shipping Context Engine and CuratorSegmentBuilder selected History, but the
final fast path had no exact final budget gate. AgentLoop routed the model
after Context assembly, injected Phase 3 recovery after assembly, and only
reacted to some provider overflow responses with the older emergency shrink.
Provider/model context limits were available in pricing metadata but were not
part of the initial Context budget. Existing Tool Runtime output limits
applied to persistence, not to all live provider-window pressure.

## 4. Current vs Final Context Map

| Boundary | Before Phase 4 | Final Phase 4 contract |
|---|---|---|
| Runtime/system/identity | Existing ContextBuilder and identity/bootstrap segments | Fixed system layers are assembled by the one ContextAssembler and protected from ordinary history trimming |
| Project/repository guidance | Existing bootstrap/project files | Remains in the fixed system prefix; no new project source of truth |
| Skills and Memory | Existing Phase A segment builders; costs were represented only indirectly in the old budget | Existing outputs remain available as L3 segments and are included in the final estimate; no Memory redesign |
| Session history | Curator received append-only candidates and could trim complete Turns | HistoryTrimmer uses complete Turn groups, priority/protection, provider-aware estimation and decision evidence |
| Recovery | Phase 3 projection was injected after Context assembly | Recovery evidence enters TurnContext before Curator/History budgeting, is marked protected, and is stripped from persisted user facts |
| Current user request | Appended as the final User message | Remains the protected current task and participates in the same final budget gate |
| Live Tool observations | Existing messages grew during AgentLoop; emergency shrink handled only some provider overflow | RuntimeContextTrimmer runs before every provider request, bounds successful/repeated output, preserves failure excerpts, and keeps protocol pairs valid |
| Provider request | Provider received the assembled list after ad-hoc overflow behavior | Provider receives only the budget-checked list from the existing AgentLoop mainline; raw Tool content is restored before Session persistence |

## 5. Context Authority Boundary

| Owner | Responsibility | Explicit non-responsibility |
|---|---|---|
| Model | Reason over visible evidence and choose the next action | It does not enforce its own context limit |
| ContextAssembler | Order layers, merge segments, build the current User message, perform the final estimate/gate, expose decisions and trace | It does not execute Tools or own Session persistence |
| Curator / HistoryTrimmer | Choose eligible Session history, preserve protected/relevant/recent groups, compact by complete Turn group, validate Tool closure | It does not become long-term Memory or execute main-agent work |
| RuntimeContextTrimmer | Bound the in-flight provider copy as Tool observations accumulate; retain protocol structure and evidence | It does not rewrite Session truth or perform semantic retrieval |
| AgentLoop | Resolve the routed model before assembly, supply the budget, run the Provider/Tool loop, and restore full Tool text before persistence | It does not delegate budget enforcement to the model |
| Session | Store durable conversation/task facts, subject to the existing persistence cap | It does not become the Context window or EffectJournal |
| RecoveryProjector | Read durable Phase 3 facts and render a bounded fresh-Turn warning | It does not replay, restore, execute, or own Context compaction |
| Existing Memory/Skill subsystems | Provide their existing segment outputs when configured | They do not gain Phase 4 authority over Context or new cross-session policy |

## 6. Context Layer Matrix

| Layer | Source | Priority | Protected? | Compaction Policy | Owner |
|---|---|---:|---|---|---|
| L0 | Runtime contract, provider Tool definitions | 1.0 | Yes | Never removed by ordinary history trimming; explicit overflow if fixed input cannot fit | ContextAssembler / AgentLoop |
| L1 | Identity, bootstrap and project/repository guidance | 1.0 | Yes as fixed system prefix | Not selected as History; remains in final gate | ContextBuilder + Phase A builders |
| L2 | Current user task and active recovery evidence | 1.0 | Yes | Current User message is never dropped; recovery is inserted before assembly and removed only from the persisted copy | AgentLoop + ContextAssembler |
| L3 | Existing Memory recall and relevant/active Skills | Existing segment priority | Segment output is fixed for the assembled request | May be absent or fall back according to existing builders; no new Memory compaction policy | Existing Memory/Skill builders + ContextAssembler |
| L4 | Recent conversational history and relevant recent Turns | Curator relevance, recency and explicit protection | Protected groups are not ordinary deletion candidates | Kept as complete Turn groups; lower-priority groups may be dropped when required | Curator / HistoryTrimmer |
| L5 | Tool observations and older raw history | Successful old/repeated observations are lowest live pressure class; recent failures are elevated | Current-turn pairs and the latest relevant failed group are retained; Tool pairs are atomic | Successful output can become a bounded excerpt/duplicate marker; old groups can be dropped only as a whole | RuntimeContextTrimmer + HistoryTrimmer |
| L6 | Existing Curator working state/archive or existing consolidation outputs | Derived, not equal to raw evidence | Not silently promoted to raw fact | No new LLM summarizer; existing derived state keeps its existing owner and boundary | Existing Curator/Memory components |

The labels are conceptual retention classes. They do not introduce a second
Context hierarchy or a new Memory authority.

## 7. Token Budget Formula

The implemented per-request calculation is:

~~~
configured_context_limit = AgentLoop.context_window_tokens
provider_context_limit = direct provider/model metadata, or existing static pricing metadata
effective_context_limit = min(configured_context_limit, provider_context_limit)
                       when provider metadata is known
                     = configured_context_limit
                       when provider metadata is unknown

reserved_output = provider.generation.max_tokens, default 4096
runtime_margin = ContextConfig.runtime_margin_tokens, default 1024

input_context_budget = max(
    0,
    effective_context_limit - reserved_output - runtime_margin,
)

available_history = max(
    0,
    input_context_budget - estimated_tool_schema_tokens - estimated_system_tokens,
)
~~~

The final Context gate estimates the complete [system, *history, user]
payload plus Tool definitions through estimate_prompt_tokens_chain and
requires:

~~~
estimated_provider_input <= input_context_budget
~~~

The same input_context_budget is used by the in-flight Tool compactor. A
non-positive protected/fixed budget is not treated as unlimited: the Runtime
raises normalized ContextBudgetError with a reason such as
protected_fixed_context_exceeds_budget or
protected_runtime_context_exceeds_budget.

## 8. Token Estimation Audit

The supported estimate chain is:

1. Use a Provider estimate_prompt_tokens(messages, tools, model) counter when
   it exists and returns a positive value.
2. Fall back to local tiktoken cl100k_base encoding.
3. Fall back to approximately one token per four characters if tokenizer
   encoding fails.

Message accounting includes text, Tool schema, Tool calls, Tool names and
IDs, reasoning content, and thinking blocks. Inline image transport is counted
as an image placeholder rather than serializing base64 bytes as text. The
estimate is a Runtime safety/accounting approximation, not a claim about every
provider's billed tokenizer or hidden serialization overhead. The estimate
source is carried in Context metadata and trace attributes.

## 9. Context Decision Matrix

Every representative decision is recorded in bounded ContextDecision data
with source, layer, estimated_tokens, priority, decision, reason, protected,
and message IDs. Assembly decisions are exposed as context_decisions; live
Tool compaction adds runtime_context_decisions and its own context.compact
span/artifact.

| Source | Estimated Cost | Decision | Reason |
|---|---|---|---|
| Runtime/system/project prefix | Complete system estimate | KEEP | Fixed contract and repository guidance are protected inputs |
| Tool definitions | Complete Tool-schema estimate | KEEP | Required to make valid Tool calls |
| Current user request | Complete current User estimate | KEEP | Current task is never ordinary History |
| Active recovery evidence | Its contribution inside the current User message | KEEP | UNKNOWN/conflict/checkpoint warnings are relevant to this fresh Turn |
| Recent/relevant Session Turn | Provider/local estimate for the group | KEEP | Relevance, explicit protection and recency make it a preferred candidate |
| Old raw Session Turn | Provider/local estimate for the group | DROP when needed | Lowest-priority deletable complete Turn under the input budget |
| Old successful Tool observation | Provider/local estimate after replacement | COMPACT | Preserve a bounded excerpt while reducing pathological live growth |
| Repeated successful Tool observation | Provider/local estimate after replacement | COMPACT | Deterministically keep the later occurrence and mark the older duplicate |
| Recent failed Tool observation | Provider/local estimate or failure excerpt | KEEP/COMPACT | Preserve failure evidence; failures are never treated as successful duplicates |
| Protected content that still exceeds the gate | Complete estimate | ERROR | Silently dropping task/system/recovery evidence would violate the contract |

Decision evidence is audit/runtime data only and is not injected into the
model's prompt.

## 10. Tool Observation Policy

The existing Tool Runtime remains responsible for execution and the existing
_save_turn limit remains responsible for the persisted Tool-result cap. Phase
4 adds only a provider-facing copy policy:

- The compactor runs before each Provider call after any mid-Turn injection.
- Successful old results are compacted to a bounded head/tail excerpt; older
  repeated successful results can become a deterministic duplicate marker with
  a short digest reference.
- Failed results retain a larger bounded excerpt, including failure evidence,
  and are never deduplicated as successful results merely because their bytes
  match another result.
- A Tool result is not removed by itself. The assistant ToolCall and its
  associated ToolResult remain a structural unit. History deletion removes a
  complete Turn group and re-validates the whole message list.
- The current Turn cannot be dropped as stale History. If a protected Tool
  observation still cannot fit after bounded compaction, the Runtime fails
  explicitly instead of manufacturing an invalid request.
- Compaction changes only the live provider copy. Before _save_turn, original
  Tool content is restored and the existing durable size cap is applied.

This is not a global one-size-fits-all ToolResult cut: result class
(successful/repeated/failing), age, relevance and protocol position affect the
action.

## 11. Summary / Compaction Boundary

Phase 4 did not add an LLM summarizer. Existing Curator working state and
existing Memory/consolidation outputs retain their existing authorities and
remain distinguishable from raw Session evidence. Context compaction is a
bounded provider-window operation; it is not a new long-term Memory write.

Curator slow-path/provider/plan failures use the existing deterministic
fallback behavior. If the deterministic fallback itself cannot satisfy the
fixed/protected gate, ContextBudgetError is returned visibly. No infinite
summarization loop is introduced.

## 12. Recovery Integration

The existing CLI --resume and existing-session --continue selectors call
AgentLoop.prepare_resume, which only loads the Phase 3 read-only
RecoveryProjector projection. It does not replay an old process, Provider,
coroutine, Tool, checkpoint or effect.

For the next fresh Turn, _recovery_context_for_turn renders the projection
before Context assembly. Therefore UNKNOWN effect IDs, workspace/conflict
warnings, missing/corrupt artifact warnings and reference-only checkpoint
warnings are part of the current User input considered by Curator and the
final budget gate. The decision evidence marks the recovery contribution as
protected. _save_turn removes the exact model-only recovery prefix (and then
the existing runtime prefix) from the persisted user fact, so Context-only
recovery evidence does not contaminate Session history.

Phase 3 ownership remains binding: COMMITTED is not blindly replayed, FAILED
is not automatically retried, UNKNOWN is never automatically replayed, and
EffectJournal remains the effect truth source.

## 13. Failure Matrix

| Input Condition | Budget Decision | What Is Retained | What Is Compacted/Dropped | Failure Behavior | Evidence/Test |
|---|---|---|---|---|---|
| Normal short task | Complete estimate below input budget | Fixed layers, current task, short history | Nothing | Provider request proceeds | test_short_history_stays_below_budget_and_reports_keep_decision; test_context_assembler_exposes_budget_estimate_and_protected_decisions |
| History below budget | Keep selected history groups | All selected complete groups and current request | Nothing | Normal assembly | Same short-history test; existing Context tests |
| History above budget | Drop lowest-priority deletable complete Turn groups | Protected/recent group and current request | Old low-priority group, including its whole Tool exchange | Re-estimate after every deletion | test_history_budget_drops_a_whole_low_priority_turn_and_keeps_tool_pair |
| Very large ToolResult | Compact live success/failure result before provider call | Call identity, bounded success excerpt or failure excerpt | Middle/old raw body in provider copy | If protected result still exceeds budget, normalized overflow | test_runtime_compaction_retains_latest_failure_and_current_task; long-task acceptance |
| Repeated ToolResult | Fingerprint successful observations | Later successful observation and both call/result structures | Older successful duplicate marker/body | Failures are excluded from success deduplication | test_runtime_compaction_deduplicates_success_without_breaking_call_result_boundary |
| Old raw history + recent evidence | Prefer recent/failure group over old successful noise | Latest failure, current task, valid pairs | Old groups/old Tool bodies as needed | Recompute shifted positions and re-check structure | test_runtime_compaction_retains_latest_failure_and_current_task; long-task acceptance |
| ToolCall/ToolResult pair near trim boundary | Treat pair/Turn as atomic | Parent call and matching result | Whole group only, never one side | Structural error becomes normalized tool_pair_integrity | History pair test; HistoryTrimmer.structural_errors assertions |
| Protected current task near budget | Protect current User message | Current task and fixed system | No silent removal | Explicit fixed/runtime protected overflow if it cannot fit | test_protected_fixed_context_overflow_is_explicit; test_runtime_protected_context_overflow_is_normalized |
| Active UNKNOWN recovery warning | Mark recovery as protected L2 current-Turn evidence | UNKNOWN effect ID, fresh-Turn warning, checkpoint/artifact evidence | Only the Context-only prefix is removed from durable Session copy | No replay; normal Context overflow remains explicit | test_unknown_recovery_evidence_is_budgeted_and_not_persisted; Phase 3 recovery contract |
| Provider with smaller context budget | Use min(configured, provider/model limit) | Content within smaller effective window | Lower-priority history/Tool bodies | Unknown/invalid metadata never enlarges the configured window | test_budget_uses_smaller_provider_limit_and_reserves_output_and_margin |
| Missing/unknown provider token limit | Use configured fallback and record source | Content within configured fallback budget | Normal priority candidates under that budget | No network-dependent limit lookup; source is explainable | test_unknown_provider_limit_is_explicit_configured_fallback |
| Curator summary/compaction failure | Use existing deterministic fallback | Protected/recent/relevant content that fallback can fit | Invalid Curator plan/old groups as required | Provider/plan failure falls back; if fixed content still over, ContextBudgetError | Existing test_curator_fallback_when_internal_agent_does_not_finish; test_curator_provider_error_records_category |
| Protected context itself exceeds budget | No degradation that silently erases protected evidence | None is falsely claimed retained; error data identifies estimate and limit | Nothing correctness-critical is silently dropped | ContextBudgetError with reason and numeric budget fields | test_protected_fixed_context_overflow_is_explicit; runtime overflow test |
| Fresh resumed Turn after Phase 3 recovery | Assemble projection before budget/Curator | New current request plus bounded recovery evidence | No old runtime object or automatic effect replay | Fresh Turn follows existing AgentLoop/Provider path | test_unknown_recovery_evidence_is_budgeted_and_not_persisted; Phase 3 fresh-runtime/CLI tests |

## 14. Mandatory Context Integrity Tests A–G

| Test | Proof | Evidence |
|---|---|---|
| A. Budget enforcement | Final estimate is asserted at or below the supported input budget after compaction/assembly | Phase 4 long-task, runtime-compaction and assembler tests |
| B. Current task retention | Current User content is protected through history/live trimming | test_runtime_compaction_retains_latest_failure_and_current_task; explicit overflow tests |
| C. Tool pair integrity | No orphan result or missing Tool result remains after selection/compaction | History and runtime tests assert structural_errors(...) == [] |
| D. Recent failure retention | Latest failed observation survives while stale successful noise is removed/bounded | test_runtime_compaction_retains_latest_failure_and_current_task |
| E. Recovery evidence retention | UNKNOWN warning/effect ID is visible in the assembled current Turn and marked protected | test_unknown_recovery_evidence_is_budgeted_and_not_persisted |
| F. Explicit overflow | Protected fixed/runtime content raises a normalized diagnostic error | test_protected_fixed_context_overflow_is_explicit; test_runtime_protected_context_overflow_is_normalized |
| G. Explainability | Representative KEEP/COMPACT/DROP decisions expose source, layer, estimated cost and reason | assembler metadata test, history/runtime decision assertions, Context trace implementation |

## 15. Long-Task Acceptance Evidence

tests/test_phase4_context_contract.py::test_real_agentloop_mainline_handles_long_deterministic_task
uses the production AgentLoop._process_message path with a deterministic fake
Provider. The scenario is:

~~~
repository repair request
  -> ten historical coding-task Turns
  -> repeated read/search-like Tool exchanges and large outputs
  -> a latest failing Tool observation
  -> current request to continue from the failure evidence
~~~

The fake Provider advertises a 16,384-token context window while the AgentLoop
is configured for 8,192 tokens, reserves 256 output tokens and configures a
512-token runtime margin. The Curator internal call intentionally returns an
invalid plan, exercising the existing deterministic fallback rather than a
model-dependent plan. The main response is then obtained through the normal
AgentLoop Provider call.

The test proves that the assembled and provider-facing message list is within
the Context metadata input budget, has no Tool structural errors, retains the
latest failure evidence/current task, and contains a DROP or COMPACT decision.
It also proves that the one main Provider call was made from the existing
AgentLoop path; it does not claim a paid-model quality result.

## 16. Mainline Bypass Audit

There is one supported Context authority on the approved path:

~~~
pico run
  -> agent_commands.run
  -> existing RuntimeAssembly
  -> AgentLoop.run_turn
  -> AgentLoop._process_message
  -> route primary model before assembly
  -> _make_token_budget / _assemble_context_messages
  -> ContextAssembler from build_context_engine
  -> _run_agent_loop RuntimeContextTrimmer
  -> Provider.chat_with_retry or chat_stream
~~~

build_context_engine returns the shipping ContextAssembler; no
ContextEngineV2 or Phase-4-only engine exists. The lower-level
pico.agent.context.ContextBuilder remains a contributor/helper and is not a
second per-Turn Context owner. ContextAssembler.owns_compaction remains true,
so AgentLoop passes the full append-only Session candidate and does not run a
competing Host consolidation path. The long-task test exercises
AgentLoop._process_message with the same engine/provider boundary, while the
CLI and existing mainline regressions remain in the authoritative manifest.

## 17. Test Evidence

### Phase 4 focused contract and regressions

~~~
python -m pytest -q tests/test_phase4_context_contract.py
~~~

The Phase 4 file contains 11 tests. The final focused set, including the
Context, History, Curator, AgentLoop, Phase 3 and checkpoint regressions,
passed:

~~~
python -m pytest -q \
  tests/test_phase4_context_contract.py \
  tests/test_history_trimmer.py \
  tests/test_context_invariants.py \
  tests/test_default_context_engine.py \
  tests/test_curator_context_engine.py \
  tests/test_phase3_recovery_contract.py \
  tests/test_runtime_checkpoint_bug2.py

76 passed in 12.63s
~~~

### Authoritative Medium+ baseline

~~~
.\scripts\run_medium_baseline.ps1
~~~

Final result after explicitly adding the Phase 4 contract file to the
PowerShell manifest:

~~~
609 passed in 70.52s (0:01:10)
~~~

The manifest remains explicit; it was not replaced with unrestricted
repository collection.

### Static checks

~~~
ruff check pico tests/test_phase4_context_contract.py  -> All checks passed!
git diff --check                            -> no whitespace errors
~~~

## 18. Files Changed

### Phase 4 production

- pico/context_engine/budget.py — provider/configured limit resolution,
  input budget value, decisions and normalized budget error.
- pico/context_engine/assembler.py — provider/model-aware final estimate,
  fixed/protected decisions, final overflow gate and context.assemble trace.
- pico/context_engine/history_trimmer.py — runtime-margin-aware History
  trimming, decision evidence, live Tool compaction and structural gate.
- pico/context_engine/segments/curator.py — fast/slow/fallback paths all
  pass through the exact validation/budget contract.
- pico/context_engine/base.py, factory.py, __init__.py — recovery field,
  provider/model wiring and stable exports.
- pico/memory_engine/base.py — backward-compatible TokenBudget fields for
  runtime margin, provider limit and input budget.
- pico/config/pico.py — configurable runtime_margin_tokens.
- pico/utils/helpers.py — bounded multimodal image accounting.
- pico/agent/loop/main.py — model-before-assembly budget wiring, live
  compactor integration, recovery-before-assembly, context-only persistence
  stripping and raw Tool restoration before Session save. This file also
  carries the pre-existing Phase 3 recovery changes.

### Phase 4 tests

- tests/test_phase4_context_contract.py — 11 deterministic budget,
  retention, integrity, recovery, explainability and real-mainline long-task
  contract tests.

### Docs

- No separate Phase 4 design document was added; this required report is the
  Phase 4 artifact. Existing Phase 0–3 docs were preserved unchanged by this
  phase.

### Baseline

- scripts/run_medium_baseline.ps1 — explicit inclusion of
  tests/test_phase4_context_contract.py.

### Report

- reports/PHASE_04_LUNA_EXECUTION_REPORT.md — this report.

The worktree also contains the user's pre-existing Phase 0–3 changes and
untracked artifacts, including the Phase 3 recovery package, CLI hook, prior
reports/docs and earlier contract tests. They were preserved and are not
reclassified as new Phase 4 design work.

## 19. Claims Proven

- The shipping Context Engine uses an explicit provider/configured effective
  context limit with output reservation and runtime margin.
- Unknown provider metadata takes an explicit configured fallback and does not
  trigger network-dependent limit discovery on the supported path.
- Final Context assembly and live Tool pressure are checked against the same
  Runtime input budget, with visible normalized overflow errors when protected
  content cannot fit.
- Retention is layered enough to distinguish fixed/current/recovery content,
  recent/relevant history, Tool observations and old raw history.
- ToolCall/ToolResult integrity is preserved through history selection, live
  compaction and complete-group dropping in the tested paths.
- Successful repeated Tool observations have a narrow deterministic duplicate
  policy; failed observations remain failure evidence and are not success
  deduplicated.
- Context decisions and bounded trace attributes expose estimates, budget,
  retention counts, compaction/drop reasons and artifacts without injecting
  audit logs into the model prompt.
- Phase 3 UNKNOWN/recovery evidence enters the fresh Turn before Context
  trimming, remains protected, and is not persisted as a user-authored fact.
- The actual pico run AgentLoop/provider path uses the single shipping
  ContextAssembler; the deterministic long-task test exercises that path.
- The final explicit Medium+ baseline is green at 609 tests, with the Phase 4
  contract included in the manifest.

## 20. Claims NOT Proven

The following are intentionally not claimed:

- perfect semantic importance ranking;
- lossless summarization;
- exact token counting for every provider/model;
- unlimited context;
- zero information loss;
- long-term Memory correctness;
- cross-session semantic retrieval;
- real-model quality improvement.

Also not proven are paid/live-provider behavior for every integration,
deterministic LLM output replay, arbitrary external-side-effect recovery, or
that a bounded excerpt preserves every semantic detail of a raw Tool result.

## 21. Final Stop Condition

Phase 4 implementation, focused tests, explicit baseline update and required
report are complete. **STOP — WRITE REPORT — WAIT FOR WEB REVIEW.**
