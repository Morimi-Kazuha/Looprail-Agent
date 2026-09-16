# PICO Medium+
## Luna MAX Execution Report — Phase 5

**Report date:** 2026-09-15  
**Repository:** D:\Agent Learning\Pico Agent  
**Working-tree HEAD:** 0ae70289b282bdc808e668bd267c7370ffd0e5b8  
**Approved demo surface:** `pico run`  
**Authoritative baseline:** `.\scripts\run_medium_baseline.ps1`

## 1. Status

**PASS — READY FOR WEB REVIEW**

Phase 5 hardens the shipping Memory subsystem in place. The existing
`MemoryStore` now owns a bounded structured-item sidecar with explicit schema,
kind, scope, verification, provenance, eligibility, deterministic identity,
deduplication, supersession, invalidation/tombstone and bounded recall. The
existing single `# Memory` segment passes those selected items through
`ContextAssembler` and the Phase 4 budget gate.

The final authoritative baseline passed **629 tests in 95.05s**. The focused
Phase 5 contract file passed **20 tests**. Phase 4 remains accepted and frozen;
the prior 609-test surface remains in the explicit manifest. No Phase 6 full
Trace/Evaluation work, Phase 7 CLI hardening, Phase 8 Evolver, TUI/channel/Cron
expansion, vector/RAG platform or Memory UI work was started. Only the
`memory.write`/`memory.recall` evidence required by this Phase was added.

After this report, Phase 5 stops and waits for Web Review.

## 2. Task and Scope

The binding distinction remains:

~~~text
Session != Memory != Context
~~~

Session stores what happened in a conversation. Memory stores only selected,
reusable knowledge that passes an explicit Runtime policy. Context decides what
of the available Memory is visible in the current provider request. The work
uses the existing Memory, Context and AgentLoop paths; it does not introduce
`MemoryEngineV2`, a second Store, a vector platform or a parallel retrieval
service.

The supported deterministic implementation deliberately does not perform
automatic LLM fact extraction. A caller may submit a structured candidate, but
an unclassified, uncertain, derived or raw operational payload is rejected by
the Runtime write gate.

## 3. Mandatory Pre-Implementation Audit

The actual shipping paths were read before implementation:

| Area | Shipping implementation found | Phase 5 conclusion |
|---|---|---|
| `pico/memory_engine/` | `Memory` was a frozen text/score/metadata carrier; `MemoryBackend` was an async opaque plugin protocol; `MemoryStore` owned `user.md` and `episodes.md` | Preserve the carrier/protocol and extend the existing Store with one structured sidecar |
| `pico/context_engine/` | `MemorySegmentBuilder` was the single composite host-memory/plugin-recall segment; `ContextAssembler` owned final message assembly and Phase 4 budgeting | Keep one Memory segment and route structured recall through it |
| `pico/session/` | Session messages are append-oriented durable conversation state | Do not use Session as the structured Memory store |
| `pico/agent/loop/` | AgentLoop assembles Context, calls Provider, saves the Session turn and dispatches the legacy backend store | Share the ContextBuilder Store with the consolidator/personalizer and keep legacy dispatch compatible |
| `pico/cli/_runtime_assembly.py` | `pico run` resolves the optional backend, constructs AgentLoop and owns start/stop | Keep null backend runnable and optional backend failures visible |
| Recovery | Phase 3 projection is fresh-Turn evidence; `_save_turn` already removes runtime-only prefixes | Prevent recovery markers from entering structured Memory or episodic persistence |
| Myna/plugin stack | Myna is an optional contribution resolved through identity checks; no bundled external Myna dependency exists | Keep Myna optional; do not make the baseline network/service-dependent |

Before Phase 5 there was no authoritative structured item schema, explicit
write eligibility gate, repository/project scope filter, lifecycle status,
deterministic structured dedupe, or bounded local structured recall. The
legacy backend still receives its compatibility conversation slice; that
interface is not treated as the host structured-fact writer.

## 4. Current vs Final Memory Map

| Boundary | Before Phase 5 | Final Phase 5 contract |
|---|---|---|
| Durable host Memory | Markdown `user.md` profile plus `episodes.md` event log | Those files remain compatible; the same `MemoryStore` additionally owns `user_memory/structured/items.jsonl` |
| Memory item | Frozen `Memory(text, score, metadata)` with no lifecycle meaning | The same frozen carrier has defaulted structured fields; `MemoryItem` is an alias, not a second model |
| Write path | Markdown helpers and opaque plugin `store(session_id, messages)` | `MemoryStore.write` canonicalizes, validates, applies eligibility, locks, deduplicates, supersedes conflicts and atomically writes |
| Verification | No host structured verification boundary | `observed`, `verified`, `user_provided`, `derived` and `uncertain`; only eligible evidence can become durable structured Memory |
| Scope | No deterministic structured repository/project filter | `turn_local`, `session_local`, `project`, `repo_local`, `global` and `user`; only durable scopes are accepted by the structured writer |
| Recall | Host Markdown selection plus optional opaque backend recall | Structured recall validates records, filters scope/status/staleness/evidence/relevance and applies bounded deterministic ranking/limits |
| Lifecycle | No structured update/supersession/invalidation contract | `update`/`update_memory`, automatic conflict supersession, `invalidate`, `tombstone`, version and retained inactive records |
| Context | Backend recall and host Markdown were combined by the Memory segment | Structured selected items join the existing `# Memory` segment, then go through the unchanged ContextAssembler Phase 4 gate |
| Null backend | No external backend meant the implicit Memory segment was a no-op | Empty local structured store keeps the old no-read fast path; explicitly written local structured items remain usable without Myna |

## 5. Memory Authority Boundary

| Owner | Responsibility | Explicit non-responsibility |
|---|---|---|
| Session | Append and retrieve conversation/task messages; preserve the existing Session format | It is not the structured reusable-fact database or provider context window |
| Memory | `MemoryStore` owns the structured item schema, write policy, provenance, scope, lifecycle, local persistence and recall diagnostics; it also retains compatible Markdown profile/episode behavior | It does not store every chat message, ToolResult, reasoning trace or Recovery object as a fact |
| Context | `MemorySegmentBuilder` renders selected host/plugin hits; `ContextAssembler` orders segments, applies the Phase 4 budget gate and produces the provider message list | It does not create facts, execute Tools or persist Session state |
| Agent | AgentLoop owns turn timing, ContextBuilder, provider/tool orchestration, Session save and legacy backend dispatch; the mainline shares one Store with MemoryConsolidator, Personalizer and Curator | It does not treat a model statement as verified Memory |
| Model | May propose a fact or use visible recalled evidence | It does not decide eligibility, scope, staleness, budget or durable truth |
| Runtime | Canonicalizes and validates items, enforces policy and boundaries, performs deterministic lifecycle operations and final Context budgeting | It does not claim semantic extraction or task success from a write/recall operation |
| Trace | Records bounded `memory.write`/`memory.recall` decisions and references | It is not a second transcript, payload store or Memory authority |

## 6. Memory Item Contract (Actual Fields and Authority)

The authoritative local representation is one JSON object per line at:

~~~text
<state>/user_memory/structured/items.jsonl
~~~

The schema identifier is `pico.memory.item.v1`. A persisted item contains:

| Field | Authority and behavior |
|---|---|
| `schema` | Exact schema marker `pico.memory.item.v1` |
| `text` | Bounded reusable claim text; this is the only claim payload rendered into Context |
| `kind` | `project_fact`, `user_preference`, `repo_convention`, `successful_procedure`, `failure_lesson`, `environment_fact` or rejected `unclassified` |
| `scope` | Explicit visibility/durability boundary |
| `verification` | Categorical evidence status; `uncertain` and `derived` do not pass the durable writer |
| `confidence` | Bounded informational string, default `unknown`; it is not fake numeric certainty and is not used to claim correctness |
| `status` | `active`, `superseded`, `invalidated`, `tombstoned` or `stale` |
| `memory_id` | Deterministic generated identity for the canonical scope/kind/key/source/content tuple |
| `normalized_key` | Deterministic lexical claim key used for conflict identity |
| `content_digest` | SHA-256 digest of normalized claim text |
| `provenance` | Bounded scalar references only; no raw logs or payloads |
| `created_at`, `updated_at` | ISO timestamps |
| `version` | Positive lifecycle version; conflict replacements increment from the active version |
| `supersedes`, `superseded_by` | Bounded lifecycle references |
| `invalidated_reason` | Bounded explicit invalidation explanation |
| `metadata` | Small allowlisted labels/policy flags only (`category`, `labels`, `tags`, `reusable`, `explicit_durable`, `transient`, `one_off`, `hypothesis`, `recovery_only`) |

The bounded provenance allowlist is `source`, `source_type`, `session_id`,
`turn_id`, `tool_name`, `result_ref`, `repo_identity`, `project_id`,
`user_id`, `observed_at`, `source_path`, `source_digest` and
`evidence_digest`. `Memory.score` remains a compatibility carrier field and
is recomputed for a recall hit; it is not the durable item authority.

## 7. Memory Lifecycle (Actual)

~~~text
candidate Memory/mapping/string
  -> canonical text/kind/scope/verification/provenance
  -> explicit eligibility gate
  -> shared Store lock + schema-validated read
  -> exact deterministic dedupe
  -> active conflict detection and versioned supersession
  -> atomic JSONL replacement
  -> bounded recall read
  -> status/scope/repository/project/user/staleness/evidence filters
  -> lexical relevance + scope/verification/freshness ranking
  -> top_k/max_chars budget
  -> existing Memory segment
  -> ContextAssembler Phase 4 estimate/gate
  -> Provider request
~~~

`update_memory` is a policy-routed replacement operation rather than an
in-place mutation. `invalidate_memory(..., tombstone=False)` keeps an explicit
invalidated record; `tombstone=True` keeps a tombstoned record. Inactive
records are never recalled. A missing `source_path` is lazily excluded as
stale; explicit invalidation and newer active conflicts are also exclusion
signals. There is no arbitrary universal TTL.

No event-sourcing log, semantic LLM dedupe, whole-store dump, vector database
or automatic model extraction was added.

## 8. Write Policy Matrix

| Input fact | Verified? | Reusable? | Scope | Write? | Runtime reason |
|---|---|---:|---|---|---|
| Verified repository convention | `verified` or `observed` | Yes | `repo_local` | Yes | Matching repository identity and allowed reusable kind |
| Verified project fact | `verified` or `observed` | Yes | `project` | Yes | Repository and project identity are explicit |
| Stable environment/tool fact | `verified` or `observed` | Yes | `repo_local`/`project` | Yes | Source/provenance can be retained as bounded reference |
| Successful reusable procedure | `verified` or `observed` | Yes | `repo_local`/`project` | Yes | Allowed `successful_procedure` kind |
| Verified failure lesson | `verified` or `observed` | Yes | `repo_local`/`project` | Yes | Only when expressed as a reusable lesson, not raw failure output |
| Explicit durable user preference | `user_provided` | Yes | `user` | Yes | User identity is required; preference kind is required |
| Model hypothesis or guess | `uncertain`/`derived` | No/unknown | Any | No | `verification_not_eligible`; model output is not self-verifying |
| One-off result or transient test failure | Any | No | Turn/session | No | Non-durable scope or explicit `one_off`/`transient` policy flag |
| Raw ToolResult/log/reasoning/recovery payload | Any | No | Any | No | Banned source type, recovery marker, or oversized/raw payload |
| Exact duplicate | Eligible candidate | Already present | Same identity tuple | No new item | Returns explainable `duplicate` result |
| Conflicting item from another repository | Eligible-looking | Yes | `repo_local`/`project` | No | `wrong_repository_scope` |

The structured writer accepts no `turn_local` or `session_local` durable item;
those classes remain explicit vocabulary for non-persisted context boundaries.

## 9. Retrieval Matrix

| Memory | Current project/repository | Status | Eligible? | Selected? | Reason |
|---|---|---|---|---|---|
| Matching `repo_local` verified convention | Same repository identity | `active`, source present | Yes | Yes when lexical relevance and budget allow | Highest local scope weight plus evidence/freshness |
| Matching `project` fact | Same repository and project identity | `active` | Yes | Yes when relevant and bounded | Project boundary is checked deterministically |
| Matching `user` preference | Same user identity | `active` | Yes | Yes when relevant and bounded | User scope is identity-bound |
| `global` reusable fact | Any repository | `active` | Yes | Yes when relevant and bounded | Global is intentionally not repository-scoped |
| Local/project fact from another repository | Different identity | `active` | No | No | `wrong_scope` |
| Project fact from another project/user-scoped fact from another user | Identity mismatch | `active` | No | No | `wrong_scope` |
| Superseded, invalidated or tombstoned item | Any | Inactive status | No | No | Lifecycle status filter |
| Active item whose `source_path` disappeared | Matching identity | Source missing | No | No | `stale:source_missing` |
| Uncertain or derived item | Matching identity | Any | No | No | Verification filter |
| Relevant item beyond `top_k` or `max_chars` | Matching identity | `active` | Candidate only | No | Deterministic retrieval budget |
| No lexical overlap with current query | Matching identity | `active` | Policy-valid but not relevant | No | Bounded lexical task relevance filter |

Ranking is deterministic and intentionally modest: lexical overlap first,
then scope weight, verification weight and freshness. It is not semantic
understanding and does not claim perfect ranking.

## 10. Contamination and Lifecycle Test Evidence

The focused file `tests/test_phase5_memory_contract.py` contains the required
A–J coverage:

| Contract | Direct evidence |
|---|---|
| A. Write eligibility | `test_A_write_policy_provenance_and_item_schema_are_explicit` |
| B. Unverified exclusion | `test_B_unverified_or_task_local_candidates_are_excluded` and `test_B_raw_tool_and_recovery_evidence_never_become_durable_memory` |
| C. Deduplication | `test_C_exact_dedupe_uses_scope_kind_key_source_and_digest`, including inactive duplicate protection |
| D. Supersession | `test_D_conflict_supersession_and_explicit_invalidation_are_retrievable`, including `update_memory` and tombstone |
| E. Cross-session reuse | `test_E_cross_session_reuse_and_cross_repository_isolation` and `test_J_real_agentloop_mainline_reuses_same_repo_and_excludes_other_repo` |
| F. Cross-project/repository isolation | Same-state repository identity filter in E; separate Repository B assertion in the real AgentLoop scenario |
| G. Context integration | `test_G_existing_memory_segment_reaches_context_assembler` and the actual CLI test below |
| H. Recovery contamination prevention | `test_H_recovery_only_and_transient_failure_inputs_do_not_contaminate_store`; Recovery-only episode append is also rejected |
| I. Failure isolation | Corrupt-schema, corrupt-AgentLoop, unavailable-local-storage and unavailable-optional-backend tests; existing legacy backend store failure test remains in baseline |
| J. Explainability | `test_J_write_and_recall_results_expose_bounded_explainability` checks bounded diagnostics and no claim text in audit metadata |

The contamination cases explicitly cover cross-repository leakage, unverified
hypotheses, Recovery warnings, missing source files, stale/conflicting facts,
duplicates, inactive lifecycle records and raw ToolResult provenance.

## 11. Context Integration and Phase 4 Boundary

The actual integration is:

~~~text
pico run
  -> AgentLoop
  -> ContextEngine factory
  -> existing MemorySegmentBuilder
  -> MemoryStore.get_memory_context / legacy backend recall
  -> ContextAssembler
  -> Phase 4 final budget/structure gate
  -> Provider
~~~

Structured items are rendered by the existing `render_recalled_memory`
function inside the one `# Memory` segment. `structured_top_k` is bounded to
64 and `structured_max_chars` to 32,000 by configuration, with defaults of 5
and 6,000. The structured budget is applied before rendering and cannot evict
the Phase 4 protected L0/L1/L2 inputs. The final ContextAssembler estimate
still sees the complete assembled system/current-user/history/tool payload and
retains the Phase 4 explicit overflow behavior.

The `pico run` entrypoint is exercised directly by
`test_J_actual_pico_run_reaches_structured_memory_segment`: a deterministic
local fact is pre-seeded, `memory.backend=null` is selected, Typer invokes the
approved `run -m` path, and the scripted Provider receives the fact in its
actual prompt input. No direct Memory-to-Provider bypass exists.

## 12. Session, Recovery and Tool Separation

Recovery evidence remains a fresh-Turn projection. Structured `write` rejects
Recovery/checkpoint markers and Recovery-looking provenance; `append_history`
also drops Recovery-only entries. Existing `_save_turn` stripping remains in
place. The corrupt-memory AgentLoop test proves that a bad local record does
not block a new Provider turn or prevent Session persistence.

`ToolResult` is not a structured Memory item. The host writer stores only an
explicit selected claim and bounded provenance reference; raw ToolResult,
reasoning and recovery source types are rejected. The legacy async
`MemoryBackend.store(session_id, messages)` seam remains because existing
plugins depend on it and receives the existing sanitized conversation slice;
it is documented and traced as compatibility ingestion, not as proof that the
host accepted a durable structured fact.

## 13. Myna / Null Backend Decision

| Classification | Decision |
|---|---|
| KEEP | Existing `MemoryBackend`, frozen `Memory` compatibility shape, plugin registry and Myna manifest identity checks |
| HARDEN | Host `MemoryStore` structured policy/lifecycle; shared Store wiring in AgentLoop/Consolidator/Curator/Personalizer; bounded trace evidence; optional-backend fail-closed behavior remains visible |
| OPTIONAL | Myna remains an optional richer external backend behind the existing `MemorySegmentBuilder`; it is not required for the core structured local contract |
| DEFER | Live Myna structured-native extraction/retrieval integration, semantic/vector ranking, LLM extraction quality and any Memory UI/evaluation redesign |

With `memory.backend=null`, an empty structured file keeps the historical
no-backend no-read behavior and Sessions/Local Skills still run. If the host
has explicitly written local structured items, the same Memory segment may
read them without Myna. An explicitly configured unavailable optional backend
raises through the existing plugin stack; it is never silently replaced with a
pretend backend. The baseline has no paid service, network Memory dependency or
bundled Myna installation.

## 14. Failure Matrix

| Mandatory case | Implemented behavior | Direct evidence |
|---|---|---|
| Backend unavailable | Plugin resolution raises `PluginNotFoundError`; no fake backend is created | `test_optional_backend_unavailable_fails_closed`; existing plugin-stack contract remains in repository |
| Corrupt memory item | Valid records can be read while malformed lines are skipped with bounded warnings; writes fail closed with `store_corrupt` | `test_I_corrupt_schema_retrieval_is_fail_closed_and_explainable` |
| Invalid schema | Record schema/required fields/digest/status are validated; invalid records never enter recall | Same corrupt-schema test |
| Duplicate | Exact scope/kind/normalized-key/source/content identity returns explainable duplicate result without another active record | `test_C_exact_dedupe_uses_scope_kind_key_source_and_digest` |
| Conflicting newer fact | Same conflict key with a later eligible fact increments version and marks older active records superseded | `test_D_conflict_supersession_and_explicit_invalidation_are_retrievable` |
| Retrieval failure | Unavailable local storage returns an empty bounded result with `storage_unavailable`; it does not fabricate hits | `test_I_unavailable_local_memory_storage_fails_closed` |
| Empty store | Empty result with zero candidates/selected diagnostics; no external dependency | `test_I_corrupt_schema_retrieval_is_fail_closed_and_explainable` |
| Oversized retrieval | `top_k` and `max_chars` are clamped; items beyond the retrieval budget are excluded | `test_F_recall_is_bounded_deterministic_and_marks_stale_sources` |
| Wrong scope | Repository/project/user identity mismatch is excluded with `wrong_scope` | `test_E_cross_session_reuse_and_cross_repository_isolation` |
| Recovery-only evidence | Rejected at structured write and episodic append boundaries | `test_B_raw_tool_and_recovery_evidence_never_become_durable_memory` and H |
| Legacy backend store failure | Existing AgentLoop behavior still saves Session before surfacing the backend error | `tests/test_agent_loop_memory_pipeline.py::test_store_failure_surfaces_after_session_is_saved` in the authoritative baseline |

Failure handling is operation-specific: local malformed/unavailable reads fail
closed with diagnostics, while the legacy external backend’s existing transport
errors continue to surface according to its frozen contract.

## 15. Explainability and Memory Trace

`memory.write` records bounded accepted/action/reason, kind, scope,
verification, item ID, content digest, deduplication and supersession count.
Its artifact contains the decision and bounded diagnostics, not the raw claim.

`memory.recall` records bounded query, scope, user/repository/project identity,
top-k, character budget, backend label, candidate count, eligible count,
selected count, stale exclusions and hit count. Hit artifacts retain only a
bounded text preview, score, identity, kind, scope, verification, status and
content digest. Structured recall diagnostics additionally expose selected IDs,
scope matches, exclusion reasons and retrieval budget. `MemorySegmentBuilder`
exposes `memory_hits`, `structured_memory_hits` and bounded structured
diagnostics in Context metadata; none of that audit detail is injected into
the prompt.

## 16. Mainline Bypass Audit

The approved path was checked at each boundary:

1. `pico/cli/agent_commands.py` enters `assemble_runtime` for `pico run`.
2. `pico/cli/_runtime_assembly.py` resolves the optional backend and constructs
   `AgentLoop`.
3. `AgentLoop` constructs one `ContextBuilder` Store, passes it to the Context
   factory and passes the same Store to `MemoryConsolidator`.
4. The factory passes that Store into `MemorySegmentBuilder` and Curator; the
   Personalizer also uses `self.context.memory` rather than reconstructing a
   parallel Store.
5. The Memory segment calls host structured recall and the existing opaque
   backend recall. Both are rendered under the one `# Memory` segment.
6. `ContextAssembler` performs the frozen Phase 4 assembly, estimate, structure
   validation and budget gate before AgentLoop calls the Provider.

There is no local structured recall call from Provider code, no direct prompt
concatenation outside the Memory segment, no Session-to-Memory whole-copy step,
and no second structured retrieval platform. Standalone legacy constructors
retain compatibility fallbacks, but the shipping AgentLoop mainline uses the
shared Store.

## 17. Compatibility and Scope Guard

Preserved interfaces and behavior include:

- existing `MemoryBackend` async methods and legacy plugin hit construction;
- frozen `Memory(text, score, metadata)` positional/source compatibility via
  defaulted new fields;
- Session, Tool Runtime, EffectJournal, Recovery, Context Engine, Provider and
  CLI selector contracts;
- null backend startup/shutdown behavior and Local Skill availability;
- Phase 4 Context budget, protected layers, Tool-pair integrity and overflow
  behavior.

The tokenizer warm-up added to `ContextAssembler` construction only moves the
one-time local tokenizer load outside the Phase-A concurrency measurement; it
does not change Context policy or budget semantics. Existing user worktree
changes from Phases 0–4 were preserved and not reset.

## 18. Files Changed

### Production

- `pico/memory_engine/backend.py` — structured vocabulary, provenance, lifecycle
  fields, write result and optional capability protocol.
- `pico/memory_engine/consolidate/consolidator.py` — single-Store structured
  JSONL lifecycle, policy, dedupe, supersession, invalidation, staleness,
  recall and Recovery filtering.
- `pico/memory_engine/__init__.py` — public structured contract exports.
- `pico/agent/context/builder.py` — repository/project/user boundary and
  repository-root wiring.
- `pico/agent/loop/main.py` — shared Store wiring, local structured budget
  inclusion and MemoryConfig boundary propagation.
- `pico/context_engine/segments/memory.py` — structured recall in the existing
  Memory segment and null/local behavior.
- `pico/context_engine/segments/curator.py` and
  `pico/context_engine/factory.py` — shared Store and structured configuration
  wiring.
- `pico/context_engine/assembler.py` — one-time tokenizer warm-up for the
  existing Phase-A concurrency contract.
- `pico/config/pico.py` — project identity and bounded structured recall config.
- `pico/tracing/semconv.py` — bounded `memory.write`/`memory.recall` evidence.

### Tests, baseline and docs

- `tests/test_phase5_memory_contract.py` — deterministic A–J/lifecycle,
  null-backend, failure and actual `pico run` coverage.
- `scripts/run_medium_baseline.ps1` — explicit Phase 5 test manifest entry.
- `docs/medium-plus-baseline.md` — Phase 5 baseline inclusion/semantics.
- `reports/PHASE_05_LUNA_EXECUTION_REPORT.md` — this report.

## 19. Verification Evidence

The final commands completed successfully:

~~~text
tests/test_phase5_memory_contract.py
20 passed in 6.46s

.\scripts\run_medium_baseline.ps1
629 passed in 95.05s (0:01:35)

ruff check on all Phase 5 touched production/test files
All checks passed!

python -m compileall -q pico tests/test_phase5_memory_contract.py
completed successfully

git diff --check
completed successfully; only Git line-ending normalization warnings were emitted
~~~

The focused suite includes no live LLM or paid/external Memory dependency.
The real Typer `pico run -m` test uses a deterministic scripted Provider and a
pre-seeded local structured item. The full baseline proves that the accepted
Phase 0–4 surface remains green after the Phase 5 changes.

## 20. Claims Proven

The current evidence proves that:

- a verified reusable structured fact can be accepted with explicit scope and
  bounded provenance;
- uncertain/derived hypotheses, raw ToolResult/reasoning/recovery evidence,
  transient/one-off flagged facts and non-durable scopes are excluded;
- deterministic exact duplicates do not create uncontrolled active records;
- an eligible conflicting replacement supersedes the older active fact and
  invalidation/tombstone removes items from recall without deleting history;
- repository/project/user scope filters prevent the tested cross-repository and
  cross-user leakage cases, while same-repository fresh Sessions reuse the
  stored fact without copying Session A history;
- recall is bounded, deterministic, explainable and stale-source aware;
- selected local Memory reaches the existing ContextAssembler and Provider
  through the actual `pico run` path;
- malformed/unavailable local Memory fails closed without blocking Session
  persistence, and unavailable optional backend resolution remains explicit;
- the authoritative baseline remains independent of Myna and external Memory
  infrastructure.

## 21. Claims NOT Proven

The following claims are intentionally not made:

- perfect semantic memory extraction
- perfect semantic retrieval
- memory is always correct
- zero stale memories
- cross-user shared memory safety
- unlimited long-term knowledge
- real-model quality improvement
- vector search superiority
- autonomous learning

Also not proven are live Myna structured-native behavior, real-model extraction
quality, semantic equivalence of paraphrased claims, provider-specific billed
token quality, or production-scale concurrent multi-process stress beyond the
existing file lock/atomic-write contract.

After this report: **STOP → WAIT FOR WEB REVIEW**.
