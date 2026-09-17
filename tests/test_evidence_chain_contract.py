"""Evidence-chain contracts.

These tests use the existing Spine/AgentLoop/LooprailBench paths.  The repository
fixture is deliberately deterministic, so a verifier result is independently
observable without making a real-model claim.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from benchmarks.looprailbench.claims import ClaimState, evaluate_layered_claim, evaluate_paired_claim
from benchmarks.looprailbench.fixtures.evidence_repository import EvidenceRepositoryPack
from benchmarks.looprailbench.harness import run
from benchmarks.looprailbench.host import RecordingOutlet, RuntimeTrialHost
from benchmarks.looprailbench.records import TrialStatus, VerificationState
from benchmarks.looprailbench.registry import PackRegistry
from benchmarks.looprailbench.report import rebuild_full_report
from benchmarks.looprailbench.schema import ClaimRule, ExecutionPolicy, ExperimentSpec
from benchmarks.looprailbench.verifier import VerifierSeal, run_sealed_verifier
from looprail.agent.loop.main import ProviderTurnError
from looprail.config.looprail import LooprailConfig
from looprail.config.schema import Config
from looprail.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from looprail.spine import ChatType, Origin, Source, TurnRequest
from looprail.tracing import semconv, trace
from looprail.tracing import spans as tracing_spans
from looprail.tracing.store import TraceStore


@pytest.fixture
def trace_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "traces"
    monkeypatch.setenv("LOOPRAIL_TRACING", "1")
    monkeypatch.setenv("LOOPRAIL_TRACING_DIR", str(root))
    tracing_spans._store = None
    yield root
    tracing_spans._store = None


class _MainlineProvider(LLMProvider):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[list[dict[str, Any]]] = []

    def get_default_model(self) -> str:
        return "scripted/phase6"

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> LLMResponse:
        del tools, model, max_tokens, temperature, reasoning_effort, tool_choice
        self.calls.append(messages)
        usage = {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9}
        if len(self.calls) == 1:
            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCallRequest(
                        id="phase6-list-dir",
                        name="list_dir",
                        arguments={"path": ".", "max_entries": 8},
                    )
                ],
                finish_reason="tool_calls",
                usage=usage,
                model="scripted/phase6",
            )
        return LLMResponse(
            content="mainline complete",
            finish_reason="stop",
            usage=usage,
            model="scripted/phase6",
        )


class _FailureLoop:
    async def run_turn(self, req, emit, drain, *, stream):
        del req, emit, drain, stream
        raise ProviderTurnError("auth")


class _Assembly:
    def __init__(self, loop: Any) -> None:
        self.agent_loop = loop

    async def close(self) -> None:
        return None


def _request(*, turn_id: str = "phase6-turn", channel: str = "bench") -> TurnRequest:
    return TurnRequest(
        origin=Origin.USER,
        source=Source(
            channel=channel,
            chat_id="phase6-chat",
            sender_id="phase6-user",
            chat_type=ChatType.DM,
        ),
        text="inspect the workspace",
        conversation=f"{channel}:phase6-chat",
        turn_id=turn_id,
    )


def _evidence_spec(
    root: Path,
    *,
    pack_id: str = "phase6-repository",
    claim_rules: tuple[ClaimRule, ...] = (),
) -> ExperimentSpec:
    return ExperimentSpec(
        suite="phase6-evidence",
        repetitions=1,
        pack_ids=(pack_id,),
        output_root=root,
        identity={
            "looprail_commit": "0" * 40,
            "provider": "deterministic",
            "model": "scripted/phase6",
        },
        execution=ExecutionPolicy(
            timeout_seconds=30.0,
            max_comparison_block_attempts=1,
        ),
        claim_rules=claim_rules,
    )


async def _run_repository_experiment(
    root: Path,
    *,
    pack: EvidenceRepositoryPack | None = None,
    claim_rules: tuple[ClaimRule, ...] = (),
):
    selected_pack = pack or EvidenceRepositoryPack()
    registry = PackRegistry()
    registry.register(selected_pack)
    ref = await run(
        _evidence_spec(root, claim_rules=claim_rules),
        registry=registry,
    )
    return ref, rebuild_full_report(ref)


@pytest.mark.asyncio
async def test_actual_runtime_mainline_writes_reconstructable_trace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    trace_root: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = Config(
        agents={
            "defaults": {
                "workspace": str(workspace),
                "model": "scripted/phase6",
                "max_tokens": 64,
                "max_tool_iterations": 2,
            },
        },
    )
    looprail_config = LooprailConfig(base=config)
    looprail_config.memory.backend = None
    provider = _MainlineProvider()
    host = await RuntimeTrialHost.build(
        config=config,
        looprail_config=looprail_config,
        provider=provider,
        cron_service=None,
        outlet=RecordingOutlet("bench"),
    )
    try:
        observation = await host.run(_request())
    finally:
        await host.close()

    assert len(provider.calls) == 2
    assert observation.outcome is not None
    assert observation.run_id
    assert observation.turn_id == "phase6-turn"
    assert observation.trace_artifact_ref
    trace_path = Path(observation.trace_artifact_ref)
    assert trace_path.is_file()

    records = TraceStore(trace_root).read_trace(observation.run_id)
    names = {record.get("name") for record in records}
    assert {"spine.turn", "session.turn", "context.assemble", "llm.call", "tool.call"}.issubset(names)
    root = next(record for record in records if record.get("name") == "spine.turn")
    attrs = root["attributes"]
    assert attrs["run.id"] == observation.run_id
    assert attrs["turn.id"] == "phase6-turn"
    assert any(
        record.get("attributes", {}).get("spine.outcome") in {"completed", "completed_with_tool_failure"}
        for record in records
    )
    assert observation.trace_summary is not None
    assert observation.trace_summary["span_count"] == len(records)


@pytest.mark.asyncio
async def test_failure_trace_is_linked_without_becoming_failure_authority(
    tmp_path: Path,
    trace_root: Path,
) -> None:
    host = RuntimeTrialHost(
        assembly=_Assembly(_FailureLoop()),
        outlet=RecordingOutlet("bench"),
    )
    try:
        observation = await host.run(_request(turn_id="phase6-provider-failure"))
    finally:
        await host.close()

    assert observation.outcome is None
    assert observation.runtime_state.value == "provider_failed"
    assert observation.failure_category == "auth"
    assert observation.run_id
    records = TraceStore(trace_root).read_trace(observation.run_id)
    root = next(record for record in records if record.get("name") == "spine.turn")
    assert root["attributes"]["spine.outcome"] == "provider_failed"
    assert root["attributes"]["spine.failure_category"] == "auth"


def test_tool_trace_records_failure_category_and_turn_link(trace_root: Path) -> None:
    with trace.span(
        "tool.call",
        root=True,
        turn_id="phase6-tool-turn",
    ) as tool_span:
        run_id = tool_span.trace_id
        semconv.tool_call(
            tool_span,
            {
                "name": "read_file",
                "params": {"path": "missing.txt"},
                "call_id": "phase6-tool-call",
                "context": SimpleNamespace(turn_id="phase6-tool-turn"),
            },
            "Error: invalid parameters: path not found",
            None,
        )

    record = TraceStore(trace_root).read_trace(run_id)[0]
    assert record["attributes"]["turn.id"] == "phase6-tool-turn"
    assert record["attributes"]["tool.failure_category"] == "tool_validation_failure"


def test_memory_trace_records_bounded_write_recall_and_turn_link(
    trace_root: Path,
    tmp_path: Path,
) -> None:
    from looprail.memory_engine import Memory, MemoryKind, MemoryScope, MemoryVerification
    from looprail.memory_engine.consolidate.consolidator import MemoryStore

    store = MemoryStore(
        tmp_path,
        repository_id="phase6-repo",
        project_id="phase6-project",
        user_id="phase6-user",
    )
    item = Memory(
        text="This repository uses pytest.",
        kind=MemoryKind.REPO_CONVENTION.value,
        scope=MemoryScope.REPO_LOCAL.value,
        verification=MemoryVerification.VERIFIED.value,
        normalized_key="test-runner",
        provenance={
            "source": "phase6-fixture",
            "source_type": "repository_observation",
            "repo_identity": "phase6-repo",
            "project_id": "phase6-project",
            "user_id": "phase6-user",
            "turn_id": "phase6-memory-turn",
        },
    )
    with trace.span(
        "spine.turn",
        root=True,
        session_key="bench:phase6-memory",
        turn_id="phase6-memory-turn",
    ) as root:
        write_result = store.write(item)
        hits = store.recall_memory(
            "pytest.",
            repo_identity="phase6-repo",
            project_id="phase6-project",
            user_id="phase6-user",
        )

    assert write_result.accepted is True
    assert hits
    records = TraceStore(trace_root).read_trace(root.trace_id)
    by_name = {record["name"]: record for record in records if record.get("name") in {"memory.write", "memory.recall"}}
    assert set(by_name) == {"memory.write", "memory.recall"}
    assert all(record["attributes"]["turn.id"] == "phase6-memory-turn" for record in by_name.values())
    assert by_name["memory.write"]["attributes"]["memory.operation"] == "write"
    assert by_name["memory.recall"]["attributes"]["memory.hits"] == 1
    recall_artifact = Path(by_name["memory.recall"]["attributes"]["memory.recall.artifact_path"])
    assert recall_artifact.is_file()
    assert "This repository uses pytest." in recall_artifact.read_text(encoding="utf-8")


class _CrashingVerifier:
    async def verify(self, workspace: Path):
        del workspace
        raise RuntimeError("verifier test process unavailable")


@pytest.mark.asyncio
async def test_verifier_crash_is_not_run_and_is_not_a_task_result(tmp_path: Path) -> None:
    sealed_task = tmp_path / "task-definition.json"
    sealed_task.write_text("{}\n", encoding="utf-8")
    result = await run_sealed_verifier(
        _CrashingVerifier(),
        workspace=tmp_path,
        seal=VerifierSeal.capture(sealed_task),
    )
    assert result.result.state is VerificationState.NOT_RUN
    assert result.infrastructure_error == "verifier_crashed:RuntimeError"
    assert result.result.verifier_id
    assert result.result.verifier_digest


def test_trace_artifact_is_bounded_redacted_and_reconstructable(trace_root: Path) -> None:
    store = TraceStore(trace_root)
    secret = "phase6-secret-value"
    artifact = store.persist_artifact(
        "llm.output",
        {"traceId": "phase6-run", "sessionKey": "bench:phase6"},
        {
            "items": ["x" * 2_048 for _ in range(64)],
            "authorization": f"Bearer {secret}",
            "reasoning_content": f"hidden chain {secret}",
        },
    )
    path = Path(artifact["path"])
    payload = path.read_text(encoding="utf-8")
    assert path.is_file()
    assert artifact["bytes"] <= store.max_artifact_bytes
    assert secret not in payload
    assert json.loads(payload)["truncated"] is True


def test_semantic_conventions_keep_reasoning_as_metadata_only() -> None:
    response = LLMResponse(
        content="visible answer",
        reasoning_content="private chain of thought that must not be persisted",
        usage={"total_tokens": 3},
    )
    output = semconv.llm_output_payload(response)
    attrs = semconv.llm_attrs(response, "scripted", "scripted/phase6")
    serialized = json.dumps(output, ensure_ascii=False)
    assert output["reasoning"]["present"] is True
    assert output["reasoning"]["chars"] > 0
    assert "private chain of thought" not in serialized
    assert "llm.reasoning_preview" not in attrs
    assert attrs["llm.reasoning_present"] is True
    assert attrs["llm.reasoning_sha256"]


def test_portable_artifact_and_budget_locks_have_one_cross_platform_path(tmp_path: Path) -> None:
    from benchmarks.looprailbench.budget import ProviderBudgetConfig, ProviderBudgetLedger

    ledger = ProviderBudgetLedger(
        tmp_path / "provider-budget.jsonl",
        ProviderBudgetConfig(
            hard_cap_cny=10.0,
            external_service_reserve_cny=0.0,
            max_total_request_attempts=2,
            max_input_tokens_per_call=100,
            max_output_tokens_per_call=100,
            input_cache_miss_usd_per_million=1.0,
            output_usd_per_million=1.0,
            conservative_usd_to_cny_multiplier=8.0,
        ),
    )
    request_id = ledger.reserve(
        trial_id="phase6-lock",
        request_digest="d" * 64,
        model="scripted/phase6",
        estimated_input_tokens=1,
    )
    ledger.settle(request_id, input_tokens=1, output_tokens=1)
    snapshot = ledger.snapshot()
    assert snapshot.accounting_complete is True
    assert ledger.lock_path.name == "provider-budget.jsonl.lock"


@pytest.mark.asyncio
async def test_sealed_verifier_identity_is_independent_of_task_self_report(tmp_path: Path) -> None:
    fixture = EvidenceRepositoryPack.fixture
    workspace = tmp_path / "workspace"
    fixture.materialize(workspace)
    verifier = fixture.verifier(workspace)
    seal = VerifierSeal.capture_many((workspace / fixture.test_path,))
    fixture.apply_strategy(workspace, "baseline")
    result = await run_sealed_verifier(verifier, workspace=workspace, seal=seal)

    assert result.infrastructure_error is None
    assert result.result.state is VerificationState.FAILED
    assert result.result.verifier_id == "repository_task_v1"
    assert result.result.verifier_digest
    assert "agent_self_report" not in result.result.metrics


@pytest.mark.asyncio
async def test_repository_pack_persists_task_trial_verifier_and_manifest_links(tmp_path: Path) -> None:
    claim_rules = (
        ClaimRule(
            rule_id="paired-improvement",
            metric="pair.phase6-repository.strategy.paired_task_pass_delta",
            operator="gt",
            threshold=0.0,
        ),
    )
    ref, report = await _run_repository_experiment(tmp_path, claim_rules=claim_rules)
    records = {
        json.loads(path.read_text(encoding="utf-8"))["key"]["variant_id"]: json.loads(
            path.read_text(encoding="utf-8")
        )
        for path in ref.root.glob("trials/**/trial-record.json")
    }
    assert set(records) == {"baseline", "candidate"}
    assert records["baseline"]["status"] == TrialStatus.TASK_FAILED.value
    assert records["baseline"]["metrics"]["agent_self_report"] == "fixed"
    assert records["baseline"]["metrics"]["verifier_observed_pass"] is False
    assert records["candidate"]["status"] == TrialStatus.PASSED.value
    assert records["candidate"]["verification"]["state"] == VerificationState.PASSED.value
    for record in records.values():
        manifest_path = ref.root / record["reproducibility_ref"]
        assert manifest_path.is_file()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["schema"] == "looprail.bench.reproducibility.v1"
        assert manifest["verifier"]["id"] == "repository_task_v1"
        assert record["reproducibility_ref"] in record["artifact_refs"]
        assert record["verifier_artifact_ref"] in record["artifact_refs"]
    assert report.ship_complete is True
    assert report.measurement_valid is True
    assert report.claim_state is ClaimState.SUPPORTED
    assert report.claim_gates["correctness"] is True
    assert report.claim_gates["regression_non_inferiority"] is True
    assert report.metrics["trial.planned"] == 2
    assert report.metrics["trial.accounted"] == 2
    assert report.metrics["trial.valid"] == 2
    assert report.metrics["trial.unaccounted"] == 0
    assert report.pair_summaries[0].valid_pairs == 1


class _InvalidMeasurementPack(EvidenceRepositoryPack):
    async def run_trial(self, context):
        execution = await super().run_trial(context)
        if context.key.variant_id == "candidate":
            return replace(execution, measurement_valid=False)
        return execution


@pytest.mark.asyncio
async def test_denominator_accounting_exposes_invalid_trial_and_blocks_claim(tmp_path: Path) -> None:
    ref, report = await _run_repository_experiment(tmp_path, pack=_InvalidMeasurementPack())
    del ref
    assert report.ship_complete is True
    assert report.measurement_valid is False
    assert report.claim_state is ClaimState.INVALID_MEASUREMENT
    assert report.metrics["trial.planned"] == 2
    assert report.metrics["trial.accounted"] == 2
    assert report.metrics["trial.valid"] == 1
    assert report.metrics["trial.invalid"] == 1
    assert report.metrics["trial.unaccounted"] == 0
    assert "invalid_trial_measurements:1" in report.findings


def test_claim_gate_rejects_regression_even_when_efficiency_improves() -> None:
    result = evaluate_paired_claim(
        baseline_passes=1,
        candidate_passes=0,
        valid_pairs=1,
        minimum_valid_pairs=1,
        efficiency_observed=0.90,
        candidate_correctness_ok=True,
    )
    assert result.state is ClaimState.REJECTED
    assert result.reason == "regression_gate_failed"
    assert result.efficiency_observed == 0.90


def test_claim_gate_marks_weak_paired_evidence_inconclusive() -> None:
    result = evaluate_paired_claim(
        baseline_passes=0,
        candidate_passes=1,
        valid_pairs=1,
        minimum_valid_pairs=2,
        efficiency_observed=0.50,
    )
    assert result.state is ClaimState.INCONCLUSIVE
    assert result.reason == "evidence_insufficient"
    assert result.positive_claim_eligible is False


def test_claim_gate_order_makes_measurement_invalid_non_compensating() -> None:
    result = evaluate_layered_claim(
        measurement_valid=False,
        correctness_ok=True,
        integrity_ok=True,
        regression_ok=True,
        evidence_sufficient=True,
        efficiency_observed=0.99,
    )
    assert result.state is ClaimState.INVALID_MEASUREMENT
    assert result.reason == "measurement_invalid"
