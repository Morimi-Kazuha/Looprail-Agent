# PICO Medium+
## Luna MAX Execution Report — Phase 8

**Report date:** 2026-09-16  
**Repository:** `<repo-root>`<br>
**Working-tree HEAD:** `0ae70289b282bdc808e668bd267c7370ffd0e5b8`  
**Approved normal Runtime surface:** `pico run`  
**Opt-in Evolution surface:** `pico evolve run|check|status|finalize`  
**Authoritative baseline command:** `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_medium_baseline.ps1`

## Status

**PASS — READY FOR WEB REVIEW**

Phase 8 hardens the existing Evolver into a controlled, evidence-backed
cross-Run loop. The deterministic acceptance path preserves the existing
candidate, child-commit/worktree, train/sealed, Phase 6 Verifier, Claim Gate,
activation and rollback authorities. A successful candidate remains
`pending_human`; this report is implementation evidence, not final project
acceptance.

The explicit Medium+ baseline grew from the frozen Phase 7 result of **653
passed** to **671 passed** because the 18-test Phase 8 contract file was added
to the manifest. The normal Runtime remains separate and does not trigger
evolution, candidate mutation, sealed evaluation, promotion or activation.

## Objective and Scope

The implemented contract is:

```text
Historical Trace / failure evidence
  -> frozen Evolution Run boundary
  -> bounded Candidate generation input
  -> immutable Candidate Manifest / patch
  -> isolated child commit and worktree
  -> train evaluation
  -> held-out sealed evaluation
  -> independent deterministic Verifier
  -> hard Claim Gate
  -> activation and rollback artifacts
  -> pending_human
```

This is a Runtime/system-level **Controlled Self-Evolution Loop**. It is not
autonomous self-improving AI, model-weight training, RL, fine-tuning or an
automatic deployment service. The candidate generator boundary is
deterministic in the acceptance fixture; the evidence ingestion and all
downstream evaluation machinery are real.

Only the existing `runtime` Candidate Label is executable end to end. Other
labels remain explicitly unsupported or partial where their canonical policy
does not provide a real mutation/evaluation chain.

## Current Evolver Map

The current code map, re-audited against implementation, is:

```text
pico evolve run/check/status/finalize
  -> pico/evolver/cli.py
  -> pico/evolver/launch/runner.py
  -> registered bench bundle / existing AppWorld entry
  -> build_evolution_orchestrator()
  -> EvolutionOrchestrator.run()
  -> existing diagnose/design/apply/gate loop
  -> Candidate + CandidateManifest / G5
  -> git_ops.commit_files_as_child()
  -> refs/evolver/<node> + node ledger
  -> git_ops.worktree_at() + EvalBackend
  -> train gate / Candidate evidence
  -> SealedTestRunner + unseal_retention()
  -> RepositoryTaskVerifier / run_sealed_verifier()
  -> Phase 6 evaluate_layered_claim()
  -> existing activation artifacts and human state machine
```

Phase 8 adds the bounded `pico/evolver/lineage.py` evidence layer around this
path. It does not create a second Evolver, benchmark, Verifier, workflow
engine, AgentLoop, TraceStore or activation authority.

Phase 7 remains the reviewer-facing normal Runtime path:

```text
pico run -> Run ID -> TraceStore evidence
```

Phase 8 consumes a selected persisted Run explicitly through the Evolution
workflow; ordinary `pico run` is not coupled to candidate generation.

## KEEP / HARDEN Decisions

| Existing component | Decision | Phase 8 treatment |
|---|---|---|
| `EvolutionOrchestrator` and round journal | KEEP | Existing diagnose/design/apply/gate lifecycle remains authoritative. |
| `Candidate` and `CandidateManifest` | HARDEN | Source lineage is bound before apply; G5 manifest preparation freezes identity and rejects post-freeze mutation. |
| `CandidateLabel` / `LABEL_POLICIES` | KEEP | Canonical runtime allowlist, fixture, evaluator and `human_review` policy are reused; unsupported labels are not widened artificially. |
| child commit / `git_ops` | KEEP | Candidates become real child commits with immutable-path checks, per-node refs and ephemeral worktrees. |
| train evaluation / `EvalBackend` | KEEP | Existing paired/focused gate and measurement-validity vocabulary remain the decision input. |
| sealed runner | KEEP | Existing `SealedTestRunner` and post-hoc `unseal_retention` own held-out execution. |
| Phase 6 Verifier | KEEP | `RepositoryTaskVerifier`, `VerifierSeal` and `run_sealed_verifier` are used directly in the acceptance fixture. |
| Claim Gate | KEEP | Existing non-compensating layered Claim Gate returns `SUPPORTED`, `REJECTED`, `INCONCLUSIVE` or invalid measurement. |
| activation / rollback | KEEP | Existing strict activation bundle and human actor state machine remain authoritative; lineage only correlates artifacts. |
| TraceStore | KEEP | `load_trace_failure_evidence()` reads a real durable JSONL Run and copies bounded failure attributes/digests. |
| candidate lineage | ADDITIVE HARDENING | `evolution_freeze.json` and per-candidate sidecars provide correlation and lifecycle evidence without making decisions or serving code. |

No component was replaced because the existing architecture could satisfy the
required controlled chain after boundary hardening.

## Controlled Self-Evolution Contract

An Evolution Run is a separately identified, opt-in engineering workflow. It
freezes baseline identity, supported label, mutable allowlist, fixture,
evaluator, disjoint train/sealed task IDs, Verifier identity, Claim Gate
identity and activation policy before candidate preparation. Candidate source
evidence is a bounded reference to a real historical Trace failure and,
optionally, a separately observed Trial/Verifier failure.

When source evidence is enabled on the production builder, an explicit
independent Verifier identity and digest are required; a deferred or missing
identity fails closed before diagnosis/design.

The candidate can propose a bounded patch. It cannot choose its own Verifier,
sealed answers, denominator, Claim Gate, baseline evidence or activation
policy. A positive train result is not activation. A positive Claim Gate is
not automatic serving-runtime mutation.

## Reflection vs Evolution

Reflection remains the within-Run behavior:

```text
attempt -> observe failure -> reason -> retry
```

It uses temporary Runtime/context state and does not create a Candidate
Manifest or Evolution Run. Controlled Self-Evolution is cross-Run:

```text
historical durable evidence -> Candidate -> persisted evaluation -> gate ->
future configuration/runtime proposal
```

The Phase 8 acceptance test starts from a finished TraceStore Run and creates a
separate Evolution Run ID; ordinary Agent retries are not relabeled as
evolution.

## Trace -> Candidate Lineage

The positive fixture uses these actual durable identities and paths:

| Artifact | Deterministic identity / location |
|---|---|
| Historical source Run | `source_run_id = trace-*`, generated by the real `TraceStore` and resolved from its durable JSONL file; the test asserts the loaded ID is the one passed to evolution. |
| Evolution Run | `phase8-evolution-run-001` |
| Frozen boundary | `<work_dir>/evolution_freeze.json` |
| Candidate | `v1-c1-p8a1` |
| Candidate lineage | `<work_dir>/lineage/v1-c1-p8a1.json` |
| Candidate node ledger | `<work_dir>/nodes/v1-c1-p8a1.json` |
| Activation bundle | `<work_dir>/activation/v1-c1-p8a1/` |
| Manifest | `activation/.../candidate_manifest.json` |
| Candidate evidence | `activation/.../evidence.json` |
| Before/after snapshots | `activation/.../before.json` and `after.json` |
| Rollback evidence | `activation/.../rollback.json` |
| Activation state | `activation/.../activation.json`, ending `pending_human` |
| Round journal | `<work_dir>/journal/rounds.jsonl` |
| Sealed records | `<work_dir>/sealed/round_0_C0.json` and `round_1_v1-c1-p8a1.json` |

The source ID is intentionally runtime-generated rather than hardcoded. The
test first writes a real `spine.turn` span with
`spine.outcome=provider_failed` and `spine.failure_category=auth`, closes the
Run, loads it through `TraceStore`, hashes its JSONL bytes and passes that
bounded object through `CandidateGenerationContext` and
`bind_candidate_to_trace()`. The lineage sidecar preserves the source Run ID,
source trace artifact, source evidence digest, Evolution Run ID, baseline SHA,
parent node, candidate ID and patch digest. This is correlation created before
evaluation, not a post-hoc winner annotation.

## Candidate Authority Matrix

| Resource | Owner | Candidate can read? | Candidate can modify? | Verifier authority |
|---|---|---:|---:|---|
| Runtime target `benchmarks/appworld/agent_cli.py` | Candidate patch + child commit apply | bounded target/baseline bytes | Yes, only under canonical runtime allowlist | Verifies behavior and required changed path |
| Verifier implementation and identity | Phase 6 `RepositoryTaskVerifier` | No through generation context | No; forbidden path and immutable guard | Owns correctness/integrity result and digest |
| Train task definitions/results | `EvalBackend` / existing train gate | Train task IDs and bounded train outcome only | No task/answer modification | Evaluates candidate/control trials |
| Sealed task definitions/answers/results | `SealedTestRunner` / unseal boundary | No sealed payload, IDs or result in generation context | No; sealed paths are forbidden | Held-out result is produced after candidate freeze |
| Claim Gate | existing `run_gates` and Phase 6 layered Claim Gate | No control over gate code or denominator | No | Combines validity, correctness, integrity, regression and evidence sufficiency |
| Activation policy | canonical `LABEL_POLICIES` + activation artifacts | Policy is frozen and visible as metadata | No | Not a Verifier input; human state transition remains required |
| Baseline | frozen cold-start baseline and Git SHA | Baseline identity/metrics are bounded decision inputs | No baseline rewrite | Compares paired evidence and regression/non-inferiority |
| Historical Trace | `TraceStore` | Bounded selected failure attributes/digests only | No Trace rewrite | Source provenance; not a correctness certificate |

The candidate generation context deliberately has no sealed task IDs, sealed
payload, expected answers, Verifier implementation bytes or sealed results.
The implementation proves this supported-path boundary; it does not claim
cryptographic secrecy.

## Candidate Label Support Matrix

| Label | Mutation path | Fixture | Verifier | Evidence | Activation policy | Claim status |
|---|---|---|---|---|---|---|
| `skill` | Canonical skill path exists in policy, but no Phase 8 executable generation/evaluation path | none in Phase 8 | none dedicated | none end to end | canonical policy only; no accepted chain | unsupported |
| `prompt` | Prompt/template policy exists, but no Phase 8 executable runtime mutation chain | none in Phase 8 | none dedicated | none end to end | canonical policy only; no accepted chain | unsupported |
| `policy` | Policy label is represented by manifest architecture, but no Phase 8 dedicated mutation/evaluation chain | none in Phase 8 | none dedicated | none end to end | `human_review` policy metadata only | unsupported |
| `runtime` | `benchmarks/appworld/agent_cli.py` through existing Candidate -> G5 -> child commit/worktree | `appworld_runtime_v1` | `appworld_focused_fisher_v1` plus independent Phase 6 repository verifier | real Trace failure, train/sealed records, manifest/evidence/lineage | `human_review` | supported for the deterministic bounded fixture |
| `model_profile` | Configuration label exists, but no Phase 8 model-profile mutation chain | none in Phase 8 | none dedicated | none end to end | `human_review` policy metadata only | unsupported |
| `route` | Configuration label exists, but no Phase 8 routing mutation chain | none in Phase 8 | none dedicated | none end to end | `human_review` policy metadata only | unsupported |

The matrix reports the existing label architecture honestly. It does not infer
support from enum membership or from a manifest that lacks a real fixture and
Verifier.

## Train vs Sealed Isolation

Train IDs are frozen before generation and are the only task IDs exposed in the
generation context. The positive fixture uses `train-runtime`; the held-out
pack uses `sealed-runtime`. `EvolutionRunFreeze` rejects overlap. The source
context contains no `sealed_task_ids` field, and the test attempts to use a
sealed payload through the normal context and is rejected by the existing
`TestLeakError` boundary.

The existing sealed runner is called only after the round journal has recorded
the candidate. Sealed results are attached to the lineage sidecar only after
`unseal_retention()` completes. This is a supported-path firewall and
post-hoc indexing step, not a cryptographic isolation claim.

## Verifier Independence

The candidate's summary and any self-report are never used as correctness
evidence. The positive candidate is checked by the existing repository
Verifier in a candidate worktree, and the test runs a separate independent
Verifier against the held-out test file. The negative test has a candidate
strategy returning the word `fixed`, while the independent Verifier still
returns `FAILED`; the candidate is not promoted by that self-report.

The Verifier also owns forbidden-path checks, baseline digest checks, required
changed paths, sealed definition sealing and bounded execution. A Verifier
crash is represented as `NOT_RUN` plus infrastructure error, not as a task
failure or success.

## Measurement Validity

The existing `measurement_validity()` contract is retained:

```text
normal task failure      -> measured / valid observation
Verifier infrastructure  -> failed / invalid measurement
missing task result      -> invalid, remains in denominator
```

The Phase 8 tests assert that a `TaskEval` with zero passes is valid, while a
Verifier infrastructure failure is invalid and names the failed task. A
missing task (`hard`) is not silently removed from the expected task pack.

## Claim Gate

The gate ordering remains non-compensating:

```text
measurement validity -> correctness -> integrity -> safety -> regression /
non-inferiority -> evidence sufficiency -> optional efficiency evidence
```

The tests assert all relevant Phase 6 outcomes:

| Evidence | Claim result |
|---|---|
| Valid positive correctness/integrity/regression with sufficient evidence | `SUPPORTED` |
| Correctness false or sealed regression | `REJECTED` |
| Point estimate positive but `evidence_sufficient=False` | `INCONCLUSIVE` |
| Measurement invalid | `INVALID_MEASUREMENT` |

Efficiency is not allowed to compensate for correctness or safety regression.

## Reward-Hacking Threat Matrix

| Attack | Status | Mechanism | Test / evidence |
|---|---|---|---|
| Delete tests | Prevented | Canonical allowlist, required changed paths and forbidden baseline/test digests | `test_candidate_allowlist_rejects_verifier_and_sealed_modification` |
| Skip difficult tasks | Prevented | Frozen task pack and hard missing-task/denominator validity | `test_self_report_and_reward_hacking_do_not_override_independent_verifier` |
| Hardcode benchmark answer | Detected | Independent repository Verifier executes the sealed contract | same self-report/Verifier test |
| Modify Verifier | Prevented | Forbidden-path/immutable guard rejects protected evaluator/Verifier targets | allowlist test |
| Modify sealed data | Prevented on supported path | Sealed test path and payload are outside allowlist and generation context | leakage test plus allowlist test |
| Reduce denominator | Prevented | Missing expected task remains invalid rather than disappearing | denominator assertion in reward-hacking test |
| Hide errors | Detected | Trial/Verifier statuses and infrastructure failures remain explicit | `test_verifier_crash_is_invalid_measurement_not_task_failure` |
| Return empty success | Detected | Empty/self-reported result cannot replace independent Verifier output | self-report negative assertion |
| Disable expensive functionality only to improve cost | Not covered | No Phase 8 cost/latency correctness guard is claimed | no positive claim made |
| Alter measurement/logging | Partially prevented | Candidate cannot mutate protected evaluator/Verifier paths; full adversarial logging attestation is out of scope | allowlist/Verifier tests; no elimination claim |

These are representative integrity checks, not a claim that reward hacking is
eliminated.

## Positive Experiment

The deterministic positive experiment runs the supported runtime path end to
end. The temporary Git subject repository contains a bounded runtime function
returning `'broken'`, a train test and a disjoint sealed test expecting
`'fixed'`.

| Stage | Observed result |
|---|---|
| Historical Run | Real TraceStore Run with `provider_failed` / `auth` failure evidence |
| Freeze | `phase8-evolution-run-001`, baseline Git SHA, runtime policy, train/sealed split and independent Verifier digest persisted |
| Candidate generation | Deterministic generator callback receives bounded source/train context and returns one `runtime` Candidate |
| Patch | `benchmarks/appworld/agent_cli.py`: return `'broken'` -> return `'fixed'`; no test or Verifier file change |
| Candidate identity | `v1-c1-p8a1`; manifest and candidate are frozen before evaluation |
| Train | Baseline `0/1`; candidate `1/1`; existing focused gate returns `accepted` |
| Sealed | Baseline `0/1`; candidate `1/1`; no observed fixture regression |
| Independent Verifier | `PASSED` with required path and sealed file integrity checks |
| Claim Gate | `SUPPORTED` for this deterministic bounded fixture |
| Activation | Existing activation bundle ends `pending_human`; serving checkout remains on baseline SHA |

This proves an executable controlled candidate chain and measured exploratory
fixture improvement. It does not prove general or statistically significant
Runtime improvement.

## Negative Experiment

The contract rejects candidates before they can become an accepted child when
they target a Verifier, evaluator, train/sealed test, hidden-answer path or
other path outside the canonical runtime allowlist. A separate sealed paired
comparison where train-like evidence wins but held-out correctness regresses
produces `REJECTED`, and the layered Claim Gate remains `REJECTED`.

The independent-Verifier negative also shows that a candidate saying `fixed`
does not override an actual `FAILED` result. The Evolver can therefore say
“No”; it does not retain only winners as its decision evidence.

## Inconclusive Evidence

The deterministic Claim Gate fixture supplies correctness, integrity and
regression positives while setting `evidence_sufficient=False`. Even with a
small positive efficiency observation, the result is `INCONCLUSIVE`, not
`SUPPORTED`. This preserves the project's statistics honesty for tiny samples.

## Candidate Manifest

The representative positive manifest is produced by the existing
`prepare_candidate_manifest()` and strict `assert_manifest_gate()` path. Its
bounded fields are:

```text
candidate_id:       v1-c1-p8a1
label:              runtime
target_files:       [benchmarks/appworld/agent_cli.py]
before/after:       manifest-owned file hashes from the parent and candidate
patch_digest:       manifest patch digest, also copied to lineage
fixture:            appworld_runtime_v1
evaluator:          appworld_focused_fisher_v1
activation_policy: human_review
source_run_id:      actual TraceStore trace-* ID in lineage sidecar
evolution_run_id:   phase8-evolution-run-001
```

The immutable Candidate fingerprint includes candidate metadata, source
lineage, manifest and file bytes. `files_of()`, `deletions_of()` and the
production evidence hook fail if the candidate is changed after freeze. A
changed identity must be represented as a new Candidate.

## Promotion vs Activation

The existing round result can promote a candidate as a future search/reference
node and can persist its train/sealed evidence. That promotion creates a child
commit/ref and evidence; it does not repoint the serving checkout. The positive
test asserts the live subject repository remains at the baseline SHA and its
runtime still returns `'broken'` after candidate evaluation.

Activation is a separate state-machine operation on the activation bundle. A
passing gate does not call `set_activation_state()` and does not alter the
serving Runtime.

## Human Gate

The positive activation artifact is `pending_human`. Moving it to `ready`
requires an explicit `human_actor` and reason; a missing actor raises. The
candidate/generator process has no path to impersonate that actor. The tested
state vocabulary remains:

```text
pending_human -> ready -> activated -> rolled_back
```

No automatic production activation is implemented or claimed.

## Rollback

The existing activation bundle writes a baseline `before.json`, candidate
`after.json` and strict `rollback.json`. The test loads the record and rejects
a corrupted rollback artifact instead of reporting a false rollback success.
The state machine and artifact semantics are covered; a real production
deployment/rollback executor and automatic production rollback are not
implemented or claimed.

## CLI and Phase 7 Boundary

The re-audited Evolver CLI remains `run`, `check`, `status` and `finalize`.
`status` stays sealed-safe; `finalize` is the explicit one-way unseal action.
Phase 8 only adds lineage indexing after the existing sealed runner has
finished. It does not redesign `pico run`, add a dashboard/TUI, or connect
normal Runtime execution to evolution.

## Mainline Bypass Audit

The positive test does not write a synthetic successful candidate JSON and call
that end to end. It uses:

```text
TraceStore trace.span / durable JSONL
  -> load_trace_failure_evidence
  -> freeze_evolution_run / CandidateGenerationContext
  -> existing Candidate + prepare_candidate_manifest + G5
  -> build_evolution_orchestrator
  -> make_git_commit_apply_fn / commit_files_as_child
  -> git_ops.worktree_at / EvalBackend
  -> existing train gate and Candidate evidence
  -> SealedTestRunner / unseal_retention
  -> RepositoryTaskVerifier / run_sealed_verifier
  -> evaluate_layered_claim
  -> create_activation_artifacts / load_activation_record
  -> pending_human
```

The only deterministic test double is the candidate-generation/model callback
and the small subject fixture. The trial, worktree, train/sealed, Verifier,
Claim Gate and activation boundaries are executed through existing code.

## Files Changed

The Phase 8 delta is separated below. Existing dirty Phase 0–7 files remain
untouched by this report and are not recounted as Phase 8 changes.

### Production

- `pico/evolver/lineage.py` — durable Trace failure evidence, frozen Evolution Run boundary, generation context, candidate lineage and post-unseal indexing.
- `benchmarks/appworld/evolve/eval.py` — source fields, candidate fingerprint/freeze, immutable extraction and G5 integration.
- `pico/evolver/orchestrator/production.py` — optional Trace-backed freeze, candidate binding/freeze enforcement and lineage artifact creation.
- `pico/evolver/orchestrator/archive.py` — bounded lineage metadata in the node ledger.
- `pico/evolver/launch/runner.py` — lineage finalization after existing sealed unseal/report flow.
- `pico/evolver/__init__.py` — package documentation for the evidence layer.

### Tests

- `tests/test_phase8_evolution_contract.py` — 18 deterministic contract tests covering the required positive, negative, inconclusive, isolation, integrity, gate, activation and rollback cases.

### Fixtures

- No new committed benchmark fixture was added. The test uses a temporary Git subject repository and the existing Phase 6 `Phase6RepositoryPack` verifier fixture.

### Docs

- `reports/PHASE_08_LUNA_EXECUTION_REPORT.md` — this report.

### Baseline

- `scripts/run_medium_baseline.ps1` — explicitly includes `tests/test_phase8_evolution_contract.py`; the manifest remains bounded and offline-capable.

### Report

- `reports/PHASE_08_LUNA_EXECUTION_REPORT.md`

## Verification Evidence

Focused Phase 8 contract:

```text
.venv\Scripts\python.exe -m pytest -q tests/test_phase8_evolution_contract.py
18 passed in 44.83s
```

Authoritative Medium+ manifest:

```text
.\scripts\run_medium_baseline.ps1
671 passed in 110.17s (0:01:50)
```

The 671 count is 653 frozen Phase 7 tests plus the 18 explicit Phase 8
contract tests. The baseline command is deterministic/offline-capable and does
not require a paid model, OpenRouter, network access or secrets.

Additional pre-finalization Evolver regression checks passed for the existing
non-symlink paths, including candidate evidence/pipeline/gates, activation
paths excluding Windows symlink-only cases, launch paths excluding the
non-executable-permission case, and lifecycle/e2e subsets. A broader focused
Evolver invocation exposed pre-existing Windows user-token limitations in
symlink creation (`WinError 1314`) and one AppWorld execute-permission test;
those unrelated environment failures are not part of the authoritative
Medium+ manifest and were not changed in Phase 8.

## Claims Proven

Evidence supports these bounded claims:

- A real persisted TraceStore failure can be loaded into a bounded Evolution
  Run source object.
- The existing runtime Candidate path can be bound to that source before
  evaluation and correlated through a durable Evolution Run/candidate lineage.
- The supported runtime Candidate Label can be represented by a strict
  manifest with real target, baseline/candidate hashes, patch digest, fixture,
  evaluator and activation policy.
- Candidate identity is frozen across manifest, train, sealed, gate and
  artifact recording; mutation is rejected.
- Candidate evaluation uses child commits and isolated worktrees without
  modifying the serving checkout.
- Train and sealed task packs are disjoint, and the supported generation view
  excludes sealed payload/result fields.
- Independent deterministic Verifier output, not candidate self-report, is
  required for correctness evidence.
- Task failure and measurement/infrastructure failure remain distinct, and
  missing expected tasks are not silently dropped.
- Hard gates can accept a bounded positive fixture, reject forbidden/regressing
  candidates, and return `INCONCLUSIVE` when evidence is insufficient.
- A gated candidate produces a human-controlled activation artifact ending in
  `pending_human`; passing does not activate the serving Runtime.
- Corrupt rollback evidence is rejected rather than reported as success.
- The explicit Medium+ baseline passes 671 tests.

## Claims NOT Proven

The following claims remain explicitly outside the evidence and must not be
made:

- continuous autonomous self-improvement;
- model-weight learning;
- fine-tuning;
- reinforcement learning;
- automatic production activation;
- automatic production rollback;
- production canary deployment;
- statistically significant general improvement;
- cross-model generalization;
- all Candidate Labels fully supported;
- sealed evaluation cryptographic isolation;
- reward-hacking elimination;
- distributed evolution;
- zero-regression guarantees.

The positive result is a deterministic bounded fixture result and an
exploratory controlled candidate chain, not a population-level or production
performance claim.

After this report: **STOP -> WAIT FOR WEB REVIEW.**

PASS — READY FOR WEB REVIEW
