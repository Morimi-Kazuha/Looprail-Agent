from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from looprail.agent.effects import (
    EffectClass,
    EffectJournal,
    EffectRecord,
    EffectStatus,
    InvalidEffectTransition,
    JournalCorruptionError,
    RecoveryAction,
    RecoveryPlanner,
    canonical_digest,
)


def _record(effect_id: str = "effect-1", status: EffectStatus = EffectStatus.PREPARED) -> EffectRecord:
    return EffectRecord(
        effect_id=effect_id,
        turn_id="turn-1",
        session_key="cli:chat",
        trace_id="trace-1",
        tool_call_id="call-1",
        tool_name="read_file",
        effect_class=EffectClass.READ,
        arguments_digest=canonical_digest({"path": "README.md"}),
        status=status,
        prepared_at="2026-01-01T00:00:00+00:00",
    )


def test_canonical_digest_is_order_independent_and_does_not_expose_payload() -> None:
    first = canonical_digest({"path": "a", "content": "secret-shaped-value"})
    second = canonical_digest({"content": "secret-shaped-value", "path": "a"})

    assert first == second
    assert "secret-shaped-value" not in first


def test_journal_round_trips_history_and_latest_state(tmp_path) -> None:
    journal = EffectJournal(tmp_path)
    prepared = _record()
    running = prepared.with_updates(status=EffectStatus.RUNNING, started_at="2026-01-01T00:00:01+00:00")
    committed = running.with_updates(status=EffectStatus.COMMITTED, finished_at="2026-01-01T00:00:02+00:00")

    journal.append(prepared)
    journal.append(prepared)  # duplicate state is harmless and remains auditable
    journal.append(running)
    journal.append(committed)

    reloaded = EffectJournal(path=journal.path)
    assert [record.status for record in reloaded.history("effect-1")] == [
        EffectStatus.PREPARED,
        EffectStatus.PREPARED,
        EffectStatus.RUNNING,
        EffectStatus.COMMITTED,
    ]
    assert reloaded.latest("effect-1") == committed


def test_journal_rejects_invalid_transition(tmp_path) -> None:
    journal = EffectJournal(tmp_path)
    prepared = _record()

    with pytest.raises(InvalidEffectTransition):
        journal.append(prepared.with_updates(status=EffectStatus.COMMITTED))

    journal.append(prepared)
    journal.append(prepared.with_updates(status=EffectStatus.RUNNING))
    journal.append(prepared.with_updates(status=EffectStatus.COMMITTED))

    with pytest.raises(InvalidEffectTransition):
        journal.append(prepared.with_updates(status=EffectStatus.RUNNING))


def test_partial_jsonl_tail_cannot_become_a_terminal_fact(tmp_path) -> None:
    journal = EffectJournal(tmp_path)
    prepared = _record()
    running = prepared.with_updates(status=EffectStatus.RUNNING)
    journal.append(prepared)
    journal.append(running)

    with journal.path.open("ab") as stream:
        stream.write(b'{"effect_id":"effect-1","status":"committed"')

    reloaded = EffectJournal(path=journal.path)
    assert reloaded.latest("effect-1").status is EffectStatus.RUNNING
    assert len(reloaded.history("effect-1")) == 2

    journal.append(running.with_updates(status=EffectStatus.COMMITTED))
    assert EffectJournal(path=journal.path).latest("effect-1").status is EffectStatus.COMMITTED


def test_middle_corruption_is_explicit(tmp_path) -> None:
    prepared = _record("effect-middle")
    running = prepared.with_updates(status=EffectStatus.RUNNING)
    path = tmp_path / "middle.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(prepared.to_dict(), ensure_ascii=False),
                "BROKEN",
                json.dumps(running.to_dict(), ensure_ascii=False),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(JournalCorruptionError):
        EffectJournal(path=path).load()


def test_middle_corruption_with_later_terminal_record_is_not_silently_recovered(tmp_path) -> None:
    prepared = _record("effect-middle-terminal")
    running = prepared.with_updates(status=EffectStatus.RUNNING)
    committed = running.with_updates(status=EffectStatus.COMMITTED)
    path = tmp_path / "middle-terminal.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(prepared.to_dict(), ensure_ascii=False),
                "BROKEN",
                json.dumps(running.to_dict(), ensure_ascii=False),
                json.dumps(committed.to_dict(), ensure_ascii=False),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(JournalCorruptionError):
        EffectJournal(path=path).load()


def test_empty_and_missing_journals_are_empty(tmp_path) -> None:
    missing = EffectJournal(path=tmp_path / "missing.jsonl")
    empty_path = tmp_path / "empty.jsonl"
    empty_path.write_text("", encoding="utf-8")

    assert missing.load() == []
    assert EffectJournal(path=empty_path).load() == []


def test_concurrent_journal_appends_preserve_complete_effect_histories(tmp_path) -> None:
    journal = EffectJournal(tmp_path)

    def append_one(index: int) -> None:
        prepared = _record(f"effect-concurrent-{index}")
        journal.append(prepared)
        journal.append(prepared.with_updates(status=EffectStatus.RUNNING))
        journal.append(prepared.with_updates(status=EffectStatus.COMMITTED))

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(append_one, range(16)))

    records = journal.load()
    assert len(records) == 48
    assert len({record.effect_id for record in records}) == 16
    assert all(journal.latest(f"effect-concurrent-{index}").status is EffectStatus.COMMITTED for index in range(16))


def test_record_serialization_uses_lowercase_stable_enum_values() -> None:
    payload = _record().to_dict()

    assert payload["schema"] == "looprail.effect.v1"
    assert payload["effect_class"] == "read"
    assert payload["status"] == "prepared"
    assert json.dumps(payload, ensure_ascii=False)


def test_recovery_planner_requires_observations_and_does_not_read_filesystem() -> None:
    record = EffectRecord(
        effect_id="effect-write",
        tool_name="write_file",
        effect_class=EffectClass.LOCAL_WRITE,
        precondition={"path": "missing.txt", "expected_pre_hash": None, "expected_exists": False},
        postcondition={"path": "missing.txt", "expected_post_hash": "post", "expected_exists": True},
    )

    decision = RecoveryPlanner.plan(record)

    assert decision.action is RecoveryAction.ASK_HUMAN
    assert "observation" in decision.reason
