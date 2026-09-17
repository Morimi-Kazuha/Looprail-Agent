from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from looprail.agent.effects import EffectClass, EffectJournal, EffectRecord, EffectStatus, JournalCorruptionError
from looprail.agent.recovery import (
    ExecutionAction,
    ObservationAction,
    ObservationCollector,
    ObservationState,
    RecoveryObservation,
    RecoveryPlanner,
    RecoveryScanner,
)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _record(
    effect_id: str,
    *,
    status: EffectStatus,
    effect_class: EffectClass = EffectClass.READ,
    result_ref: str | None = None,
    receipt_ref: str | None = None,
    precondition: dict | None = None,
    postcondition: dict | None = None,
) -> EffectRecord:
    return EffectRecord(
        effect_id=effect_id,
        turn_id="turn-1",
        session_key="test:chat",
        trace_id="trace-1",
        tool_call_id=f"call-{effect_id}",
        tool_name="read_file" if effect_class is EffectClass.READ else "write_file",
        effect_class=effect_class,
        arguments_digest="argument-digest",
        status=status,
        prepared_at="2026-01-01T00:00:00+00:00",
        started_at="2026-01-01T00:00:01+00:00" if status is not EffectStatus.PREPARED else None,
        finished_at="2026-01-01T00:00:02+00:00"
        if status in {EffectStatus.COMMITTED, EffectStatus.FAILED, EffectStatus.UNKNOWN}
        else None,
        precondition=precondition,
        postcondition=postcondition,
        result_ref=result_ref,
        receipt_ref=receipt_ref,
    )


def _local_record(
    effect_id: str,
    target: Path,
    *,
    expected_pre_hash: str | None,
    expected_exists: bool,
    expected_post_hash: str,
    status: EffectStatus = EffectStatus.RUNNING,
) -> EffectRecord:
    return _record(
        effect_id,
        status=status,
        effect_class=EffectClass.LOCAL_WRITE,
        precondition={
            "path": str(target),
            "expected_pre_hash": expected_pre_hash,
            "expected_exists": expected_exists,
        },
        postcondition={
            "path": str(target),
            "expected_post_hash": expected_post_hash,
            "expected_exists": True,
        },
    )


def _scan(record: EffectRecord, *, observation_source=None):
    return RecoveryScanner([record]).scan(observation_source=observation_source)


def test_prepared_read_is_candidate_and_only_allows_explicit_retry() -> None:
    candidate = _scan(_record("effect-prepared", status=EffectStatus.PREPARED))[0]

    assert candidate.observation_state is ObservationState.MISSING
    plan = RecoveryPlanner.plan(candidate, ObservationCollector().collect(candidate))
    assert plan.execution_action is ExecutionAction.RETRY_ALLOWED
    assert plan.observation_action is ObservationAction.NONE


def test_running_read_is_candidate_and_only_allows_explicit_retry() -> None:
    candidate = _scan(_record("effect-running", status=EffectStatus.RUNNING))[0]

    plan = RecoveryPlanner.plan(candidate, ObservationCollector().collect(candidate))
    assert plan.execution_action is ExecutionAction.RETRY_ALLOWED
    assert plan.requires_human is False


def test_local_write_pre_hash_allows_retry(tmp_path) -> None:
    target = tmp_path / "result.txt"
    target.write_text("before", encoding="utf-8")
    record = _local_record(
        "effect-local-pre",
        target,
        expected_pre_hash=_digest("before"),
        expected_exists=True,
        expected_post_hash=_digest("after"),
    )

    candidate = _scan(record)[0]
    observation = ObservationCollector().collect(candidate)
    plan = RecoveryPlanner.plan(candidate, observation)

    assert observation.matches_precondition is True
    assert observation.matches_postcondition is False
    assert plan.execution_action is ExecutionAction.RETRY_ALLOWED
    assert target.read_text(encoding="utf-8") == "before"


def test_local_write_post_hash_is_no_replay_and_does_not_write(tmp_path) -> None:
    target = tmp_path / "result.txt"
    target.write_text("after", encoding="utf-8")
    record = _local_record(
        "effect-local-post",
        target,
        expected_pre_hash=_digest("before"),
        expected_exists=True,
        expected_post_hash=_digest("after"),
    )
    before = target.read_bytes()

    candidate = _scan(record)[0]
    observation = ObservationCollector().collect(candidate)
    plan = RecoveryPlanner.plan(candidate, observation)

    assert observation.matches_postcondition is True
    assert plan.execution_action is ExecutionAction.NO_REPLAY
    assert plan.observation_action is ObservationAction.OBSERVATION_UNAVAILABLE
    assert target.read_bytes() == before


def test_local_write_missing_file_still_matches_precondition(tmp_path) -> None:
    target = tmp_path / "new.txt"
    record = _local_record(
        "effect-missing-pre",
        target,
        expected_pre_hash=None,
        expected_exists=False,
        expected_post_hash=_digest("new"),
    )

    observation = ObservationCollector().collect(_scan(record)[0])

    assert observation.exists is False
    assert observation.matches_precondition is True
    assert observation.matches_postcondition is False


def test_local_write_missing_file_matches_expected_postcondition(tmp_path) -> None:
    target = tmp_path / "new.txt"
    target.write_text("new", encoding="utf-8")
    record = _local_record(
        "effect-missing-post",
        target,
        expected_pre_hash=None,
        expected_exists=False,
        expected_post_hash=_digest("new"),
    )

    observation = ObservationCollector().collect(_scan(record)[0])

    assert observation.exists is True
    assert observation.matches_precondition is False
    assert observation.matches_postcondition is True


def test_local_write_missing_file_created_by_third_party_is_conflict(tmp_path) -> None:
    target = tmp_path / "new.txt"
    target.write_text("third-party", encoding="utf-8")
    record = _local_record(
        "effect-missing-conflict",
        target,
        expected_pre_hash=None,
        expected_exists=False,
        expected_post_hash=_digest("new"),
    )

    candidate = _scan(record)[0]
    observation = ObservationCollector().collect(candidate)
    plan = RecoveryPlanner.plan(candidate, observation)

    assert observation.matches_precondition is False
    assert observation.matches_postcondition is False
    assert plan.execution_action is ExecutionAction.ASK_HUMAN


def test_local_write_read_failure_is_unknown_not_missing(tmp_path, monkeypatch) -> None:
    target = tmp_path / "result.txt"
    target.write_text("before", encoding="utf-8")
    record = _local_record(
        "effect-read-failure",
        target,
        expected_pre_hash=_digest("before"),
        expected_exists=True,
        expected_post_hash=_digest("after"),
    )

    import looprail.agent.recovery.observations as observation_module

    monkeypatch.setattr(observation_module, "_file_hash", lambda _path: None)
    observation = ObservationCollector().collect(_scan(record)[0])
    plan = RecoveryPlanner.plan(_scan(record)[0], observation)

    assert observation.kind == "LOCAL_FILE_READ_ERROR"
    assert observation.exists is None
    assert observation.matches_precondition is None
    assert observation.matches_postcondition is None
    assert plan.execution_action is ExecutionAction.ASK_HUMAN


def test_committed_available_observation_is_candidate_but_not_replayed() -> None:
    record = _record("effect-committed-result", status=EffectStatus.COMMITTED, result_ref="result-1")

    candidate = _scan(record)[0]
    observation = ObservationCollector().collect(candidate)
    plan = RecoveryPlanner.plan(candidate, observation)

    assert candidate.observation_state is ObservationState.AVAILABLE
    assert plan.execution_action is ExecutionAction.NO_REPLAY
    assert plan.observation_action is ObservationAction.RESUME_WITH_OBSERVATION
    assert plan.requires_human is False


def test_committed_without_result_is_unavailable_and_never_fabricated() -> None:
    record = _record("effect-committed-missing-result", status=EffectStatus.COMMITTED)

    candidate = _scan(record)[0]
    observation = ObservationCollector().collect(candidate)
    plan = RecoveryPlanner.plan(candidate, observation)

    assert candidate.observation_state is ObservationState.UNAVAILABLE
    assert plan.execution_action is ExecutionAction.NO_REPLAY
    assert plan.observation_action is ObservationAction.OBSERVATION_UNAVAILABLE
    assert plan.result_ref is None
    assert plan.requires_human is True


def test_failed_without_observation_is_candidate_and_not_replayed_for_opaque_effect() -> None:
    record = _record("effect-failed-external", status=EffectStatus.FAILED, effect_class=EffectClass.EXTERNAL)

    candidate = _scan(record)[0]
    plan = RecoveryPlanner.plan(candidate, ObservationCollector().collect(candidate))

    assert candidate.observation_state is ObservationState.UNAVAILABLE
    assert plan.execution_action is ExecutionAction.ASK_HUMAN
    assert plan.observation_action is ObservationAction.OBSERVATION_UNAVAILABLE


@pytest.mark.parametrize("effect_class", [EffectClass.EXTERNAL, EffectClass.EXECUTE, EffectClass.UNKNOWN])
def test_opaque_running_effects_ask_human_without_query(effect_class: EffectClass) -> None:
    record = _record(f"effect-{effect_class.value}", status=EffectStatus.RUNNING, effect_class=effect_class)
    candidate = _scan(record)[0]

    observation = ObservationCollector().collect(candidate)
    plan = RecoveryPlanner.plan(candidate, observation)

    assert observation.kind == "UNKNOWN"
    assert observation.evidence["query_performed"] is False
    assert plan.execution_action is ExecutionAction.ASK_HUMAN
    assert plan.requires_human is True


def test_consumed_terminal_observation_is_not_a_candidate() -> None:
    record = _record("effect-consumed", status=EffectStatus.COMMITTED, result_ref="result-1")
    consumed = RecoveryObservation(
        effect_id=record.effect_id,
        effect_class=record.effect_class,
        result_ref="result-1",
        observation_state=ObservationState.CONSUMED,
    )

    assert _scan(record, observation_source={record.effect_id: consumed}) == []


def test_observation_and_plan_identities_are_stable_across_scans() -> None:
    record = _record("effect-stable", status=EffectStatus.RUNNING)
    scanner = RecoveryScanner([record])
    collector = ObservationCollector()

    first_candidate = scanner.scan()[0]
    second_candidate = scanner.scan()[0]
    first_observation = collector.collect(first_candidate)
    second_observation = collector.collect(second_candidate)
    first_plan = RecoveryPlanner.plan(first_candidate, first_observation)
    second_plan = RecoveryPlanner.plan(second_candidate, second_observation)

    assert first_candidate.candidate_id == second_candidate.candidate_id
    assert first_candidate.candidate_fingerprint == second_candidate.candidate_fingerprint
    assert first_observation.observation_id == second_observation.observation_id
    assert first_plan.recovery_id == second_plan.recovery_id
    assert first_plan.plan_hash == second_plan.plan_hash


def test_workspace_evidence_change_changes_observation_and_plan_identity(tmp_path) -> None:
    target = tmp_path / "result.txt"
    target.write_text("before", encoding="utf-8")
    record = _local_record(
        "effect-changing-observation",
        target,
        expected_pre_hash=_digest("before"),
        expected_exists=True,
        expected_post_hash=_digest("after"),
    )
    candidate = _scan(record)[0]
    collector = ObservationCollector()
    before = collector.collect(candidate)
    before_plan = RecoveryPlanner.plan(candidate, before)

    target.write_text("after", encoding="utf-8")
    after = collector.collect(candidate)
    after_plan = RecoveryPlanner.plan(candidate, after)

    assert before.observation_id != after.observation_id
    assert before_plan.recovery_id != after_plan.recovery_id
    assert before_plan.plan_hash != after_plan.plan_hash


def test_journal_corruption_fails_closed_without_partial_candidates(tmp_path) -> None:
    record = _record("effect-corrupt", status=EffectStatus.PREPARED)
    path = tmp_path / "journal.jsonl"
    path.write_text(json.dumps(record.to_dict()) + "\nBROKEN\n", encoding="utf-8")

    with pytest.raises(JournalCorruptionError):
        RecoveryScanner(EffectJournal(path=path)).scan()


def test_legacy_record_gets_compatibility_logical_effect_id() -> None:
    record = _record("effect-legacy", status=EffectStatus.RUNNING)

    candidate = _scan(record)[0]

    assert not hasattr(record, "logical_effect_id")
    assert candidate.logical_effect_id == record.effect_id
    assert candidate.attempt == record.attempt


def test_collector_does_not_execute_or_modify_local_workspace(tmp_path) -> None:
    target = tmp_path / "result.txt"
    target.write_text("before", encoding="utf-8")
    record = _local_record(
        "effect-read-only",
        target,
        expected_pre_hash=_digest("before"),
        expected_exists=True,
        expected_post_hash=_digest("after"),
    )
    before = target.read_bytes()

    observation = ObservationCollector().collect(_scan(record)[0])

    assert observation.kind == "LOCAL_FILE"
    assert target.read_bytes() == before


def test_plan_and_candidate_contain_digests_and_refs_not_tool_payload() -> None:
    record = _record("effect-no-payload", status=EffectStatus.COMMITTED, result_ref="result-ref")
    candidate = _scan(record)[0]
    plan = RecoveryPlanner.plan(candidate, ObservationCollector().collect(candidate))

    serialized = json.dumps({"candidate": candidate.to_dict(), "plan": plan.to_dict()})
    assert "argument-digest" in serialized
    assert "result-ref" in serialized
    assert "secret-tool-argument" not in serialized
