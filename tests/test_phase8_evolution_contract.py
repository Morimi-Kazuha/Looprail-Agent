"""Phase 8 contract tests for the existing Controlled Self-Evolution Loop.

The positive test intentionally crosses the real TraceStore -> existing
Evolver builder -> child commit/worktree -> RepositoryTaskVerifier -> train
gate -> SealedTestRunner -> activation/lineage boundaries.  Only the model
generation callback is deterministic test input; no successful artifact is
written directly as a shortcut around the production machinery.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from benchmarks.appworld.evolve.eval import (
    Candidate,
    CandidateFrozenError,
    deletions_of,
    files_of,
    prepare_candidate_manifest,
)
from benchmarks.picobench.claims import ClaimState, evaluate_layered_claim
from benchmarks.picobench.fixtures.phase6_repository import Phase6RepositoryPack
from benchmarks.picobench.records import VerificationState
from benchmarks.picobench.verifier import (
    RepositoryTaskVerifier,
    VerifierSeal,
    run_sealed_verifier,
    verifier_identity,
)
from pico.evolver.activation import load_activation_record
from pico.evolver.analysis.stability_bucket import StabilityBucket, TaskStability
from pico.evolver.candidate_manifest import ManifestGateError
from pico.evolver.lineage import (
    CandidateGenerationContext,
    EvolutionRunFreeze,
    LineageError,
    TraceFailureEvidence,
    TrialFailureEvidence,
    bind_candidate_to_trace,
    finalize_candidate_lineages,
    freeze_evolution_run,
    load_candidate_lineage,
    load_trace_failure_evidence,
    update_candidate_lineage,
)
from pico.evolver.orchestrator.config import Budget, OrchestratorConfig, Termination
from pico.evolver.orchestrator.gates.paired import paired_lift
from pico.evolver.orchestrator.gates.policy import CandidateOutcome, FrozenColdStartBaseline
from pico.evolver.orchestrator.gates.strategies import FocusedFisherGate
from pico.evolver.orchestrator.loop import EvolutionOrchestrator
from pico.evolver.orchestrator.production import (
    build_evolution_orchestrator,
    make_sealed_runner,
    make_worktree_eval_fn,
)
from pico.evolver.orchestrator.scoring import (
    EvalBackend,
    EvaluationVerdict,
    MeasurementFailure,
    TaskEval,
    measurement_validity,
)
from pico.evolver.orchestrator.sealed.runner import (
    TestLeakError,
    assert_no_test_leak,
    unseal_retention,
)
from pico.evolver.orchestrator.state.journal import RoundJournal
from pico.evolver.scheduler.anchor_selection import simple_anchor
from pico.evolver.tree import git_ops
from pico.evolver.tree.node import HarnessNode, NodeStatus
from pico.tracing import trace
from pico.tracing import spans as tracing_spans


TARGET = "benchmarks/appworld/agent_cli.py"
TRAIN_TEST = "tests/test_phase8_train.py"
SEALED_TEST = "tests/test_phase8_sealed.py"


@pytest.fixture
def phase8_trace_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "trace-state"
    monkeypatch.setenv("PICO_TRACING", "1")
    monkeypatch.setenv("PICO_TRACING_DIR", str(root))
    tracing_spans._store = None
    yield root
    tracing_spans._store = None


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _subject_repo(tmp_path: Path, *, sealed_expected: str = "fixed") -> tuple[Path, str]:
    repo = tmp_path / "subject"
    (repo / "benchmarks/appworld").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / TARGET).write_text(
        "def runtime_value():\n    return 'broken'\n",
        encoding="utf-8",
    )
    (repo / TRAIN_TEST).write_text(
        "import importlib.util\n"
        "from pathlib import Path\n\n"
        "_spec = importlib.util.spec_from_file_location(\n"
        "    'phase8_agent_cli', Path(__file__).parents[1] / 'benchmarks/appworld/agent_cli.py'\n"
        ")\n"
        "_module = importlib.util.module_from_spec(_spec)\n"
        "_spec.loader.exec_module(_module)\n\n\n"
        "def test_runtime_train_defect_is_fixed():\n"
        "    assert _module.runtime_value() == 'fixed'\n",
        encoding="utf-8",
    )
    (repo / SEALED_TEST).write_text(
        "import importlib.util\n"
        "from pathlib import Path\n\n"
        "_spec = importlib.util.spec_from_file_location(\n"
        "    'phase8_agent_cli_sealed', Path(__file__).parents[1] / 'benchmarks/appworld/agent_cli.py'\n"
        ")\n"
        "_module = importlib.util.module_from_spec(_spec)\n"
        "_spec.loader.exec_module(_module)\n\n\n"
        "def test_runtime_sealed_contract():\n"
        f"    assert _module.runtime_value() == {sealed_expected!r}\n",
        encoding="utf-8",
    )
    _git(repo, "init", "-q")
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Pico Phase8",
        "GIT_AUTHOR_EMAIL": "phase8@example.invalid",
        "GIT_COMMITTER_NAME": "Pico Phase8",
        "GIT_COMMITTER_EMAIL": "phase8@example.invalid",
    }
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=env)
    subprocess.run(["git", "commit", "-qm", "phase8 fixture"], cwd=repo, check=True, env=env)
    return repo, _git(repo, "rev-parse", "HEAD")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _verifier(
    repo_or_workspace: Path,
    *,
    test_path: str,
    base_digests: dict[str, str],
) -> RepositoryTaskVerifier:
    del repo_or_workspace
    return RepositoryTaskVerifier(
        test_args=("-q", test_path),
        required_changed_paths=(TARGET,),
        forbidden_paths=(test_path,),
        baseline_digests={TARGET: base_digests[TARGET], test_path: base_digests[test_path]},
        timeout_seconds=20.0,
    )


def _score_worktree_factory(repo: Path):
    base_digests = {
        TARGET: _sha(repo / TARGET),
        TRAIN_TEST: _sha(repo / TRAIN_TEST),
        SEALED_TEST: _sha(repo / SEALED_TEST),
    }

    def score(workspace: Path, _node: HarnessNode, task_ids: list[str], k: int, split: str):
        test_path = TRAIN_TEST if split == "train" else SEALED_TEST
        results: dict[str, TaskEval] = {}
        for task_id in task_ids:
            passes = 0
            infra = 0
            for _ in range(k):
                verifier = _verifier(workspace, test_path=test_path, base_digests=base_digests)
                seal = VerifierSeal.capture_many((workspace / test_path,))
                try:
                    execution = asyncio.run(
                        run_sealed_verifier(verifier, workspace=workspace, seal=seal)
                    )
                except (OSError, RuntimeError):
                    execution = None
                if execution is None or execution.infrastructure_error is not None:
                    infra += 1
                elif execution.result.state is VerificationState.PASSED:
                    passes += 1
            results[task_id] = TaskEval(
                task_id=task_id,
                passes=passes,
                attempts=k,
                infra_attempts=infra,
                failure=MeasurementFailure.infrastructure if infra else None,
            )
        return results

    return base_digests, score


def _make_source_evidence(
    trace_root: Path,
    tmp_path: Path,
) -> tuple[TraceFailureEvidence, TrialFailureEvidence]:
    with trace.span("spine.turn", root=True, turn_id="phase8-run-a-turn") as root:
        root.set(
            {
                "run.id": root.trace_id,
                "spine.outcome": "provider_failed",
                "spine.failure_category": "auth",
            }
        )
        source_run_id = root.trace_id

    trial_workspace = tmp_path / "source-trial"
    fixture = Phase6RepositoryPack.fixture
    fixture.materialize(trial_workspace)
    verifier = fixture.verifier(trial_workspace)
    seal = VerifierSeal.capture_many((trial_workspace / fixture.test_path,))
    fixture.apply_strategy(trial_workspace, "baseline")
    execution = asyncio.run(
        run_sealed_verifier(verifier, workspace=trial_workspace, seal=seal)
    )
    assert execution.infrastructure_error is None
    assert execution.result.state is VerificationState.FAILED
    trial_failure = TrialFailureEvidence(
        status="task_failed",
        verifier_id=str(execution.result.verifier_id),
        verifier_digest=str(execution.result.verifier_digest),
        findings=execution.result.findings,
        measurement_valid=True,
    )
    evidence = load_trace_failure_evidence(
        trace_root,
        source_run_id,
        trial_failure=trial_failure,
    )
    return evidence, trial_failure


def _positive_run(tmp_path: Path, trace_root: Path) -> dict[str, Any]:
    repo, base_sha = _subject_repo(tmp_path, sealed_expected="fixed")
    source, trial_failure = _make_source_evidence(trace_root, tmp_path)
    work_dir = tmp_path / "evolution"
    base_digests, score_worktree = _score_worktree_factory(repo)
    base_verifier = _verifier(repo, test_path=TRAIN_TEST, base_digests=base_digests)
    verifier_meta = verifier_identity(base_verifier)
    evolution_id = "phase8-evolution-run-001"
    freeze = freeze_evolution_run(
        work_dir,
        evolution_run_id=evolution_id,
        baseline_sha=base_sha,
        source=source,
        train_task_ids=["train-runtime"],
        sealed_task_ids=["sealed-runtime"],
        verifier_id=verifier_meta["id"],
        verifier_digest=verifier_meta["digest"],
    )
    generation_context = CandidateGenerationContext.from_freeze(freeze)
    candidate = Candidate(
        files={TARGET: b"def runtime_value():\n    return 'fixed'\n"},
        why="runtime_fixture",
        focused_task_ids=["train-runtime"],
        summary="fix the Runtime defect observed in Run A",
    )
    bind_candidate_to_trace(candidate, generation_context)
    generator_inputs: list[dict[str, Any]] = []

    stability = {
        "train-runtime": TaskStability(
            task_id="train-runtime",
            passes=0,
            attempts=1,
            bucket=StabilityBucket.STABLE_FAIL,
        )
    }
    backend = EvalBackend(
        train_task_ids=["train-runtime"],
        test_task_ids=["sealed-runtime"],
        eval=make_worktree_eval_fn(repo, score_worktree),
        cold_start=lambda: stability,
        anchor=lambda affinity=None: simple_anchor(stability),
    )
    baseline = {"train-runtime": TaskEval("train-runtime", passes=0, attempts=1)}

    def prepare(node_id: str, _parent_id: str, parent_sha: str, value: Candidate) -> None:
        prepare_candidate_manifest(node_id, parent_sha, value, repo_root=repo)

    def diagnose_of(_root: HarnessNode):
        return (lambda _round, _parent: {"source_run_id": source.source_run_id}), None

    def design_of(_sha_of, _history, _archive_summary):
        def design(_round, failure_map, _parent):
            generator_inputs.append(
                {
                    "source_run_id": generation_context.source.source_run_id,
                    "source_evidence_digest": generation_context.source.evidence_digest,
                    "failure_map": failure_map,
                    "context": generation_context.to_dict(),
                }
            )
            return [candidate]

        return design

    config = OrchestratorConfig(
        repo_root=repo,
        work_dir=work_dir,
        driver_llm_spec={},
        k_screen=1,
        k_confirm=1,
        budget=Budget(recombinations_per_round=0),
        termination=Termination(patience=1, max_rounds=1),
    )
    orchestrator = build_evolution_orchestrator(
        config,
        repo_root=repo,
        base_sha=base_sha,
        root_node_id="C0",
        backend=backend,
        gate_policy=FocusedFisherGate(k=1),
        diagnose_of=diagnose_of,
        design_of=design_of,
        baseline_of=lambda: FrozenColdStartBaseline(baseline),
        files_of=files_of,
        deletions_of=deletions_of,
        prepare_candidate=prepare,
        applied_patch_of=lambda value: value.applied_patch,
        run_gate0=False,
        evolution_run_id=evolution_id,
        source_evidence=source,
        verifier_identity=verifier_meta,
        node_id_salt="p8a1",
    )
    root = HarnessNode(
        node_id="C0",
        parent_id=None,
        git_commit_sha=base_sha,
        git_branch="phase8",
        created_at=HarnessNode.utc_now(),
        created_at_iter=0,
    )
    journal = RoundJournal(work_dir / "journal" / "rounds.jsonl")
    result = orchestrator.run("C0", journal=journal, root_node=root)
    outcome = result.rounds[0].outcomes[0]
    assert outcome.verdict is EvaluationVerdict.accepted
    assert candidate.candidate_id == "v1-c1-p8a1"
    assert generator_inputs and generator_inputs[0]["source_run_id"] == source.source_run_id

    sealed = make_sealed_runner(backend, work_dir / "sealed", k=1)
    retention = unseal_retention(
        sealed,
        journal.load(),
        vanilla_node=root,
        vanilla_train=0.0,
    )
    candidate_sha = json.loads(
        (work_dir / "nodes" / f"{candidate.candidate_id}.json").read_text(encoding="utf-8")
    )["git_commit_sha"]
    with git_ops.worktree_at(repo, candidate_sha) as candidate_workspace:
        independent = _verifier(
            candidate_workspace,
            test_path=SEALED_TEST,
            base_digests=base_digests,
        )
        verified = asyncio.run(
            run_sealed_verifier(
                independent,
                workspace=candidate_workspace,
                seal=VerifierSeal.capture_many((candidate_workspace / SEALED_TEST,)),
            )
        )
    assert verified.infrastructure_error is None
    assert verified.result.state is VerificationState.PASSED
    claim = evaluate_layered_claim(
        measurement_valid=True,
        correctness_ok=verified.result.state is VerificationState.PASSED,
        integrity_ok=True,
        regression_ok=(retention.best_test or 0.0) >= (retention.vanilla_test or 0.0),
        evidence_sufficient=True,
    )
    assert claim.state is ClaimState.SUPPORTED

    changed = finalize_candidate_lineages(work_dir, work_dir / "sealed")
    assert changed == 1
    lineage = load_candidate_lineage(work_dir, candidate.candidate_id)
    activation = load_activation_record(
        work_dir / "activation" / candidate.candidate_id,
        repo_root=repo,
    )
    assert lineage.source_run_id == source.source_run_id
    assert lineage.patch_digest == candidate.manifest.patch_digest
    assert lineage.train["verdict"] == "accepted"
    assert lineage.sealed["status"] == "measured"
    assert lineage.sealed["pass_at_1"] == 1.0
    assert lineage.activation_state == "pending_human"
    assert activation["state"] == "pending_human"
    assert _git(repo, "rev-parse", "HEAD") == base_sha
    return {
        "repo": repo,
        "base_sha": base_sha,
        "source": source,
        "trial_failure": trial_failure,
        "work_dir": work_dir,
        "candidate": candidate,
        "candidate_sha": candidate_sha,
        "retention": retention,
        "claim": claim,
        "activation": activation,
        "lineage": lineage,
    }


def test_trace_lineage_requires_real_durable_failure(
    phase8_trace_root: Path,
    tmp_path: Path,
) -> None:
    evidence, trial_failure = _make_source_evidence(phase8_trace_root, tmp_path)
    assert evidence.source_run_id
    assert Path(evidence.trace_artifact).is_file()
    assert evidence.trial_failure == trial_failure
    assert evidence.failure_signals[0]["spine.outcome"] == "provider_failed"
    assert "reasoning" not in json.dumps(evidence.to_dict())


def test_trace_lineage_rejects_absent_or_successful_source(
    phase8_trace_root: Path,
    tmp_path: Path,
) -> None:
    with pytest.raises(LineageError, match="missing|empty"):
        load_trace_failure_evidence(phase8_trace_root, "missing-run")
    with trace.span("spine.turn", root=True, turn_id="phase8-success") as root:
        root.set({"spine.outcome": "completed"})
        success_id = root.trace_id
    with pytest.raises(LineageError, match="no failure"):
        load_trace_failure_evidence(phase8_trace_root, success_id)


def test_evolution_freeze_is_immutable_and_train_sealed_are_disjoint(
    phase8_trace_root: Path,
    tmp_path: Path,
) -> None:
    source, _ = _make_source_evidence(phase8_trace_root, tmp_path)
    _, base_sha = _subject_repo(tmp_path)
    freeze = freeze_evolution_run(
        tmp_path / "freeze",
        evolution_run_id="phase8-freeze-001",
        baseline_sha=base_sha,
        source=source,
        train_task_ids=["train"],
        sealed_task_ids=["sealed"],
    )
    assert EvolutionRunFreeze.from_dict(freeze.to_dict()) == freeze
    with pytest.raises(LineageError, match="immutable|differs"):
        freeze_evolution_run(
            tmp_path / "freeze",
            evolution_run_id="phase8-freeze-001",
            baseline_sha=base_sha,
            source=source,
            train_task_ids=["train"],
            sealed_task_ids=["sealed-other"],
        )
    with pytest.raises(LineageError, match="disjoint"):
        freeze_evolution_run(
            tmp_path / "bad-freeze",
            evolution_run_id="phase8-freeze-002",
            baseline_sha=base_sha,
            source=source,
            train_task_ids=["same"],
            sealed_task_ids=["same"],
        )


def test_generation_context_does_not_expose_sealed_payload(
    phase8_trace_root: Path,
    tmp_path: Path,
) -> None:
    source, _ = _make_source_evidence(phase8_trace_root, tmp_path)
    _, base_sha = _subject_repo(tmp_path)
    freeze = freeze_evolution_run(
        tmp_path / "freeze",
        evolution_run_id="phase8-context-001",
        baseline_sha=base_sha,
        source=source,
        train_task_ids=["train"],
        sealed_task_ids=["sealed"],
    )
    context = CandidateGenerationContext.from_freeze(freeze)
    public = context.to_dict()
    assert "sealed_task_ids" not in public
    assert "sealed" not in public
    assert not hasattr(context, "sealed_task_ids")
    with pytest.raises(TestLeakError):
        assert_no_test_leak(
            anchor_task_ids=["sealed"],
            train_task_ids=["train"],
            sealed_test_ids=["sealed"],
        )


def test_candidate_freeze_rejects_mutation_between_manifest_and_evaluation(tmp_path: Path) -> None:
    repo, base_sha = _subject_repo(tmp_path)
    candidate = Candidate(
        files={TARGET: b"def runtime_value():\n    return 'fixed'\n"},
        why="runtime_fixture",
    )
    prepare_candidate_manifest("phase8-freeze-candidate", base_sha, candidate, repo_root=repo)
    assert candidate.is_frozen
    candidate.files[TARGET] = b"def runtime_value():\n    return 'tampered'\n"
    with pytest.raises(CandidateFrozenError, match="changed after freeze"):
        files_of(candidate)
    with pytest.raises(CandidateFrozenError, match="changed after freeze"):
        candidate.assert_frozen()


def test_candidate_allowlist_rejects_verifier_and_sealed_modification(tmp_path: Path) -> None:
    repo, base_sha = _subject_repo(tmp_path)
    for forbidden in (
        "pico/evolver/candidate_manifest.py",
        "benchmarks/appworld/evolve/grade.py",
        TRAIN_TEST,
        SEALED_TEST,
        "tests/test_phase8_hidden_answer.json",
    ):
        candidate = Candidate(files={forbidden: b"tampered\n"}, why="runtime_fixture")
        with pytest.raises((ManifestGateError, ValueError)):
            prepare_candidate_manifest("phase8-forbidden", base_sha, candidate, repo_root=repo)


def test_measurement_validity_keeps_task_failure_and_rejects_infrastructure() -> None:
    task_failure = measurement_validity(
        {"task": TaskEval("task", passes=0, attempts=1)},
        ["task"],
        expected_attempts=1,
    )
    infra_failure = measurement_validity(
        {
            "task": TaskEval(
                "task",
                passes=0,
                attempts=1,
                infra_attempts=1,
                failure=MeasurementFailure.infrastructure,
            )
        },
        ["task"],
        expected_attempts=1,
    )
    assert task_failure.valid is True
    assert task_failure.status.value == "measured"
    assert infra_failure.valid is False
    assert infra_failure.status.value == "failed"
    assert infra_failure.infrastructure_failures == ("task",)


def test_verifier_crash_is_invalid_measurement_not_task_failure(tmp_path: Path) -> None:
    class CrashingVerifier:
        verifier_id = "phase8-crashing-verifier"

        async def verify(self, workspace: Path):
            del workspace
            raise RuntimeError("deterministic verifier crash")

    sealed = tmp_path / "sealed-definition.json"
    sealed.write_text("{}\n", encoding="utf-8")
    result = asyncio.run(
        run_sealed_verifier(
            CrashingVerifier(),
            workspace=tmp_path,
            seal=VerifierSeal.capture(sealed),
        )
    )
    assert result.result.state is VerificationState.NOT_RUN
    assert result.infrastructure_error == "verifier_crashed:RuntimeError"
    assert result.result.state is not VerificationState.FAILED


def test_claim_gate_is_non_compensating_and_weak_evidence_is_inconclusive() -> None:
    rejected = evaluate_layered_claim(
        measurement_valid=True,
        correctness_ok=False,
        integrity_ok=True,
        regression_ok=True,
        evidence_sufficient=True,
        efficiency_observed=0.01,
    )
    inconclusive = evaluate_layered_claim(
        measurement_valid=True,
        correctness_ok=True,
        integrity_ok=True,
        regression_ok=True,
        evidence_sufficient=False,
        efficiency_observed=0.01,
    )
    invalid = evaluate_layered_claim(
        measurement_valid=False,
        correctness_ok=True,
        integrity_ok=True,
        regression_ok=True,
        evidence_sufficient=True,
    )
    assert rejected.state is ClaimState.REJECTED
    assert inconclusive.state is ClaimState.INCONCLUSIVE
    assert invalid.state is ClaimState.INVALID_MEASUREMENT


def test_train_win_with_sealed_regression_is_rejected() -> None:
    control = {"sealed": TaskEval("sealed", passes=1, attempts=1)}
    candidate = {"sealed": TaskEval("sealed", passes=0, attempts=1)}
    paired = paired_lift(
        candidate_evals=candidate,
        control_evals=control,
        task_ids=["sealed"],
        expected_attempts=1,
    )
    claim = evaluate_layered_claim(
        measurement_valid=True,
        correctness_ok=False,
        integrity_ok=True,
        regression_ok=paired.candidate_mean >= paired.control_mean,
        evidence_sufficient=True,
    )
    assert paired.verdict is EvaluationVerdict.rejected
    assert claim.state is ClaimState.REJECTED


def test_positive_trace_to_candidate_to_sealed_to_pending_human(
    phase8_trace_root: Path,
    tmp_path: Path,
) -> None:
    result = _positive_run(tmp_path, phase8_trace_root)
    assert result["source"].source_run_id != "phase8-evolution-run-001"
    assert result["trial_failure"].status == "task_failed"
    assert result["retention"].best_test == 1.0
    assert result["claim"].state is ClaimState.SUPPORTED
    assert result["activation"]["state"] == "pending_human"


def test_candidate_manifest_binds_actual_hashes_and_lineage(
    phase8_trace_root: Path,
    tmp_path: Path,
) -> None:
    result = _positive_run(tmp_path, phase8_trace_root)
    candidate: Candidate = result["candidate"]
    manifest = candidate.manifest
    assert manifest is not None
    assert manifest.label.value == "runtime"
    assert manifest.target_files == (TARGET,)
    assert manifest.fixture == "appworld_runtime_v1"
    assert manifest.evaluator == "appworld_focused_fisher_v1"
    assert manifest.activation_policy.value == "human_review"
    assert manifest.patch_digest == result["lineage"].patch_digest
    assert result["lineage"].source_evidence_digest == result["source"].evidence_digest


def test_promotion_does_not_mutate_serving_checkout(
    phase8_trace_root: Path,
    tmp_path: Path,
) -> None:
    result = _positive_run(tmp_path, phase8_trace_root)
    repo: Path = result["repo"]
    assert _git(repo, "rev-parse", "HEAD") == result["base_sha"]
    assert (repo / TARGET).read_text(encoding="utf-8").strip().endswith("'broken'")
    assert result["activation"]["state"] == "pending_human"


def test_human_activation_actor_is_required_and_is_explicit(
    phase8_trace_root: Path,
    tmp_path: Path,
) -> None:
    result = _positive_run(tmp_path, phase8_trace_root)
    artifact = result["work_dir"] / "activation" / result["candidate"].candidate_id
    from pico.evolver.activation import ActivationState, set_activation_state

    with pytest.raises(ValueError, match="human_actor"):
        set_activation_state(artifact, ActivationState.ready)
    ready = set_activation_state(
        artifact,
        ActivationState.ready,
        human_actor="reviewer@example.invalid",
        reason="Phase 8 fixture review",
    )
    assert ready["state"] == "ready"
    assert ready["state_history"][-1]["human_actor"] == "reviewer@example.invalid"


def test_corrupt_rollback_artifact_cannot_be_reported_as_success(
    phase8_trace_root: Path,
    tmp_path: Path,
) -> None:
    result = _positive_run(tmp_path, phase8_trace_root)
    artifact = result["work_dir"] / "activation" / result["candidate"].candidate_id
    rollback = artifact / "rollback.json"
    rollback.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_activation_record(artifact, repo_root=result["repo"])


def test_self_report_and_reward_hacking_do_not_override_independent_verifier(tmp_path: Path) -> None:
    fixture = Phase6RepositoryPack.fixture
    workspace = tmp_path / "workspace"
    fixture.materialize(workspace)
    verifier = fixture.verifier(workspace)
    seal = VerifierSeal.capture_many((workspace / fixture.test_path,))
    self_report = fixture.apply_strategy(workspace, "baseline")
    result = asyncio.run(run_sealed_verifier(verifier, workspace=workspace, seal=seal))
    assert self_report == "fixed"
    assert result.result.state is VerificationState.FAILED
    assert result.result.state is not VerificationState.PASSED
    # Representative denominator/test-integrity attack: a candidate cannot
    # make a missing task disappear from the hard gate.
    gate = measurement_validity(
        {"easy": TaskEval("easy", passes=1, attempts=1)},
        ["easy", "hard"],
        expected_attempts=1,
    )
    assert gate.valid is False
    assert gate.missing == ("hard",)


def test_candidate_lineage_identity_is_immutable_but_lifecycle_can_be_updated(
    phase8_trace_root: Path,
    tmp_path: Path,
) -> None:
    result = _positive_run(tmp_path, phase8_trace_root)
    work_dir: Path = result["work_dir"]
    candidate: Candidate = result["candidate"]
    before = load_candidate_lineage(work_dir, candidate.candidate_id)
    update_candidate_lineage(
        work_dir,
        candidate.candidate_id,
        sealed={"status": "measured", "result_available": True},
    )
    after = load_candidate_lineage(work_dir, candidate.candidate_id)
    assert after.candidate_id == before.candidate_id
    assert after.patch_digest == before.patch_digest
    assert after.activation_state == before.activation_state
    assert after.sealed == {"status": "measured", "result_available": True}


def test_nonaccepted_evolver_outcome_is_retained_as_no(tmp_path: Path) -> None:
    stability = {
        "task": TaskStability(
            task_id="task",
            passes=0,
            attempts=1,
            bucket=StabilityBucket.STABLE_FAIL,
        )
    }
    backend = EvalBackend(
        train_task_ids=["task"],
        test_task_ids=[],
        eval=lambda _node, ids, k, _job, **_kwargs: {
            task_id: TaskEval(task_id, 0, k) for task_id in ids
        },
        cold_start=lambda: stability,
        anchor=lambda affinity=None: simple_anchor(stability),
    )

    class RejectingGate:
        def decide(self, ctx):
            return CandidateOutcome(
                ctx.node.node_id,
                NodeStatus.pruned_at_confirm,
                verdict=EvaluationVerdict.rejected,
            )

    orchestrator = EvolutionOrchestrator(
        OrchestratorConfig(
            repo_root=tmp_path,
            work_dir=tmp_path / "rejected-run",
            driver_llm_spec={},
            termination=Termination(patience=1, max_rounds=1),
        ),
        backend=backend,
        diagnose_fn=lambda _round, _parent: {},
        design_fn=lambda _round, _map, _parent: [],
        apply_fn=lambda _parent, _candidate, _round: (_ for _ in ()).throw(AssertionError("no candidate")),
        gate_policy=RejectingGate(),
        baseline_provider=FrozenColdStartBaseline({"task": TaskEval("task", 0, 1)}),
    )
    result = orchestrator.run("C0")
    assert result.rounds[0].outcomes == []
