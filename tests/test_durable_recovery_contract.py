"""Durable recovery, checkpoint and fresh-runtime contract tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from looprail.agent.effects import EffectClass, EffectJournal, EffectRecord, EffectStatus
from looprail.agent.recovery import (
    ExecutionAction,
    RecoveryArtifactStatus,
    RecoveryProjector,
    RecoveryStateStore,
)
from looprail.session.manager import SessionManager

SESSION_KEY = "cli:recovery-contract"


def _record(
    effect_id: str,
    *,
    status: EffectStatus = EffectStatus.PREPARED,
    effect_class: EffectClass = EffectClass.READ,
    tool_name: str | None = None,
    result_ref: str | None = None,
    precondition: dict | None = None,
    postcondition: dict | None = None,
    session_key: str = SESSION_KEY,
    turn_id: str = "turn-old",
) -> EffectRecord:
    return EffectRecord(
        effect_id=effect_id,
        turn_id=turn_id,
        session_key=session_key,
        trace_id="trace-old",
        tool_call_id=f"call-{effect_id}",
        tool_name=tool_name or ("read_file" if effect_class is EffectClass.READ else "write_file"),
        effect_class=effect_class,
        arguments_digest=f"args-{effect_id}",
        status=status,
        prepared_at="2026-01-01T00:00:00+00:00",
        started_at="2026-01-01T00:00:01+00:00" if status is not EffectStatus.PREPARED else None,
        finished_at=(
            "2026-01-01T00:00:02+00:00"
            if status in {EffectStatus.COMMITTED, EffectStatus.FAILED, EffectStatus.UNKNOWN}
            else None
        ),
        precondition=precondition,
        postcondition=postcondition,
        result_ref=result_ref,
    )


def _append_effect(journal: EffectJournal, record: EffectRecord) -> None:
    prepared = record.with_updates(
        status=EffectStatus.PREPARED,
        started_at=None,
        finished_at=None,
    )
    journal.append(prepared)
    if record.status is EffectStatus.RUNNING:
        journal.append(record)
    elif record.status in {EffectStatus.COMMITTED, EffectStatus.FAILED, EffectStatus.UNKNOWN}:
        journal.append(record.with_updates(status=EffectStatus.RUNNING, finished_at=None))
        journal.append(record)


def _persist_session(root: Path, key: str = SESSION_KEY) -> None:
    manager = SessionManager(root)
    session = manager.get_or_create(key)
    session.add_message("user", "continue the interrupted task")
    manager.save(session)


def test_clean_completed_turn_is_projected_without_recovery_work(tmp_path: Path) -> None:
    _persist_session(tmp_path)
    RecoveryStateStore(tmp_path).save_turn(
        session_key=SESSION_KEY,
        turn_id="turn-complete",
        status="completed",
    )

    state = RecoveryProjector(tmp_path, workspace=tmp_path).project(SESSION_KEY)

    assert state.session_exists is True
    assert state.last_turn_id == "turn-complete"
    assert state.last_turn_status == "completed"
    assert state.plans == ()
    assert state.automatic_replay_effect_ids == ()
    assert state.artifact_status is RecoveryArtifactStatus.AVAILABLE
    assert "fresh Turn" in state.prompt_block()


@pytest.mark.parametrize(
    ("status", "effect_class", "result_ref", "expected"),
    [
        (EffectStatus.PREPARED, EffectClass.READ, None, ExecutionAction.RETRY_ALLOWED),
        (EffectStatus.RUNNING, EffectClass.READ, None, ExecutionAction.RETRY_ALLOWED),
        (EffectStatus.COMMITTED, EffectClass.READ, "result-ref", ExecutionAction.NO_REPLAY),
        (EffectStatus.FAILED, EffectClass.EXTERNAL, None, ExecutionAction.ASK_HUMAN),
        (EffectStatus.UNKNOWN, EffectClass.EXTERNAL, None, ExecutionAction.ASK_HUMAN),
    ],
)
def test_effect_state_recovery_is_conservative(
    tmp_path: Path,
    status: EffectStatus,
    effect_class: EffectClass,
    result_ref: str | None,
    expected: ExecutionAction,
) -> None:
    _persist_session(tmp_path)
    journal = EffectJournal(tmp_path)
    _append_effect(
        journal,
        _record(
            f"effect-{status.value}",
            status=status,
            effect_class=effect_class,
            result_ref=result_ref,
        ),
    )

    state = RecoveryProjector(tmp_path, workspace=tmp_path).project(SESSION_KEY)

    assert len(state.plans) == 1
    assert state.plans[0].execution_action is expected
    assert state.automatic_replay_effect_ids == ()
    if status is EffectStatus.UNKNOWN:
        assert state.unknown_effect_ids == (f"effect-{status.value}",)


def test_local_write_reconciles_post_pre_and_conflict_without_writing(tmp_path: Path) -> None:
    target = tmp_path / "result.txt"
    target.write_text("third-party", encoding="utf-8")
    _persist_session(tmp_path)
    journal = EffectJournal(tmp_path)
    import hashlib

    pre_hash = hashlib.sha256(b"before").hexdigest()
    post_hash = hashlib.sha256(b"after").hexdigest()

    _append_effect(
        journal,
        _record(
            "effect-post",
            effect_class=EffectClass.LOCAL_WRITE,
            status=EffectStatus.RUNNING,
            precondition={
                "path": str(target),
                "expected_exists": True,
                "expected_pre_hash": pre_hash,
            },
            postcondition={
                "path": str(target),
                "expected_exists": True,
                "expected_post_hash": post_hash,
            },
        ),
    )
    before = target.read_bytes()
    post_state = RecoveryProjector(tmp_path, workspace=tmp_path).project(SESSION_KEY)
    assert post_state.plans[0].execution_action is ExecutionAction.ASK_HUMAN
    assert target.read_bytes() == before


def test_local_write_hash_evidence_proves_retry_and_post_skip(tmp_path: Path) -> None:
    target = tmp_path / "result.txt"
    target.write_text("before", encoding="utf-8")
    _persist_session(tmp_path)
    journal = EffectJournal(tmp_path)

    # Hashes are calculated here instead of embedding file contents in recovery
    # state; the production projector only reads the current bytes.
    import hashlib

    pre_hash = hashlib.sha256(b"before").hexdigest()
    post_hash = hashlib.sha256(b"after").hexdigest()
    _append_effect(
        journal,
        _record(
            "effect-local",
            effect_class=EffectClass.LOCAL_WRITE,
            status=EffectStatus.RUNNING,
            precondition={
                "path": str(target),
                "expected_exists": True,
                "expected_pre_hash": pre_hash,
            },
            postcondition={
                "path": str(target),
                "expected_exists": True,
                "expected_post_hash": post_hash,
            },
        ),
    )
    state = RecoveryProjector(tmp_path, workspace=tmp_path).project(SESSION_KEY)
    assert state.plans[0].execution_action is ExecutionAction.RETRY_ALLOWED
    assert target.read_text(encoding="utf-8") == "before"

    target.write_text("after", encoding="utf-8")
    state_after = RecoveryProjector(tmp_path, workspace=tmp_path).project(SESSION_KEY)
    assert state_after.plans[0].execution_action is ExecutionAction.NO_REPLAY
    assert target.read_text(encoding="utf-8") == "after"


def test_missing_and_corrupt_recovery_artifact_fail_closed_but_derive_effects(tmp_path: Path) -> None:
    _persist_session(tmp_path)
    journal = EffectJournal(tmp_path)
    _append_effect(journal, _record("effect-missing-artifact", status=EffectStatus.RUNNING))

    missing = RecoveryProjector(tmp_path, workspace=tmp_path).project(SESSION_KEY)
    assert missing.artifact_status is RecoveryArtifactStatus.MISSING
    assert missing.plans

    store = RecoveryStateStore(tmp_path)
    store.save_turn(session_key=SESSION_KEY, turn_id="turn-old", status="interrupted")
    store.path_for(SESSION_KEY).write_text("{not-json", encoding="utf-8")
    corrupt = RecoveryProjector(tmp_path, workspace=tmp_path).project(SESSION_KEY)
    assert corrupt.artifact_status is RecoveryArtifactStatus.CORRUPT
    assert corrupt.plans
    assert "recovery_artifact_corrupt" in corrupt.warnings


def test_missing_session_and_checkpoint_are_reported_without_creating_session(tmp_path: Path) -> None:
    store = RecoveryStateStore(tmp_path)
    store.save_turn(
        session_key="cli:missing",
        turn_id="turn-old",
        status="interrupted",
        checkpoint_id="checkpoint-missing",
    )

    state = RecoveryProjector(
        tmp_path,
        workspace=tmp_path,
        checkpoint_dir=tmp_path / "shadow.git",
    ).project("cli:missing")

    assert state.session_exists is False
    assert state.checkpoint_status == "missing"
    assert "session_missing" in state.warnings
    assert "checkpoint_missing" in state.warnings
    assert not SessionManager(tmp_path).exists("cli:missing")


def test_agent_resume_projection_is_injected_as_a_fresh_turn_without_replay(tmp_path: Path) -> None:
    _persist_session(tmp_path)
    journal = EffectJournal(tmp_path)
    _append_effect(
        journal,
        _record(
            "effect-unknown",
            status=EffectStatus.UNKNOWN,
            effect_class=EffectClass.EXTERNAL,
            tool_name="send_email",
        ),
    )
    RecoveryStateStore(tmp_path).save_turn(
        session_key=SESSION_KEY,
        turn_id="turn-old",
        status="interrupted",
        checkpoint_id="checkpoint-old",
    )

    # Exercise the narrow AgentLoop integration without constructing providers,
    # sandboxes, MCP clients, or a live runtime.
    from looprail.agent.loop import AgentLoop

    agent = AgentLoop.__new__(AgentLoop)
    agent.recovery_projector = RecoveryProjector(tmp_path, workspace=tmp_path)
    agent._resume_projections = {}
    agent._pending_recovery = {}
    projection = agent.prepare_resume(SESSION_KEY)
    messages = [{"role": "user", "content": "continue"}]
    agent._inject_durable_recovery_block(SESSION_KEY, messages, {})

    assert projection.unknown_effect_ids == ("effect-unknown",)
    assert "fresh Turn" in messages[-1]["content"]
    assert "effect-unknown" in messages[-1]["content"]
    assert "do not auto-replay" in messages[-1]["content"]
    assert agent._resume_projections == {}


def test_recovery_trace_evidence_carries_session_task_effect_checkpoint_and_new_turn(tmp_path: Path) -> None:
    _persist_session(tmp_path)
    _append_effect(
        EffectJournal(tmp_path),
        _record("effect-trace-unknown", status=EffectStatus.UNKNOWN, effect_class=EffectClass.EXTERNAL),
    )
    RecoveryStateStore(tmp_path).save_turn(
        session_key=SESSION_KEY,
        turn_id="turn-old",
        status="interrupted",
        checkpoint_id="checkpoint-old",
    )

    state = RecoveryProjector(tmp_path, workspace=tmp_path).project(SESSION_KEY)
    attributes = state.trace_attributes(new_turn_id="turn-new")

    assert attributes["recovery.resumed_continuation"] is True
    assert attributes["recovery.task"] == "looprail.run.resume"
    assert attributes["recovery.session_key"] == SESSION_KEY
    assert attributes["recovery.durable_state_inspected"] is True
    assert attributes["recovery.unknown_effect_ids"] == ["effect-trace-unknown"]
    assert attributes["recovery.checkpoint_used"] is True
    assert attributes["recovery.checkpoint_restored"] is False
    assert attributes["recovery.new_turn"] is True
    assert attributes["recovery.new_turn_id"] == "turn-new"


def test_agent_loop_persists_turn_marker_without_copying_effect_facts(tmp_path: Path) -> None:
    from looprail.agent.loop import AgentLoop, TurnOutcome

    agent = AgentLoop.__new__(AgentLoop)
    agent.recovery_projector = RecoveryProjector(tmp_path, workspace=tmp_path)
    agent._persist_recovery_turn(
        SESSION_KEY,
        "turn-interrupted",
        TurnOutcome(
            status="interrupted",
            checkpoint_id="checkpoint-1",
            edited_files=["src/app.py"],
        ),
    )

    marker = RecoveryStateStore(tmp_path).load(SESSION_KEY)
    assert marker is not None
    assert marker.last_turn_id == "turn-interrupted"
    assert marker.last_turn_status == "interrupted"
    assert marker.checkpoint_id == "checkpoint-1"
    assert marker.checkpoint_files == ("src/app.py",)
    assert "effect_id" not in marker.to_dict()


def test_fresh_runtime_process_loads_durable_state_and_constructs_new_turn(tmp_path: Path) -> None:
    root = tmp_path / "cross-process"
    root.mkdir()
    repo_root = Path(__file__).resolve().parents[1]
    writer = r'''
import sys
from pathlib import Path
from looprail.agent.effects import EffectClass, EffectJournal, EffectRecord, EffectStatus
from looprail.agent.recovery import RecoveryStateStore
from looprail.session.manager import SessionManager

root = Path(sys.argv[1])
key = "cli:cross-process"
session = SessionManager(root).get_or_create(key)
session.add_message("user", "resume this task")
SessionManager(root).save(session)
journal = EffectJournal(root)
journal.append(EffectRecord(
    effect_id="effect-cross-process",
    turn_id="turn-runtime-a",
    session_key=key,
    tool_name="read_file",
    effect_class=EffectClass.READ,
    status=EffectStatus.PREPARED,
    arguments_digest="args-cross-process",
))
RecoveryStateStore(root).save_turn(
    session_key=key,
    turn_id="turn-runtime-a",
    status="interrupted",
)
'''
    subprocess.run(
        [sys.executable, "-c", writer, str(root)],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )

    reader = r'''
import json
import sys
from pathlib import Path
from looprail.agent.recovery import RecoveryProjector
from looprail.spine.message import ChatType, Source
from looprail.spine.turn import Origin, TurnRequest

root = Path(sys.argv[1])
key = "cli:cross-process"
state = RecoveryProjector(root, workspace=root).project(key)
request = TurnRequest(
    origin=Origin.USER,
    source=Source(channel="cli", chat_id="cross-process", sender_id="user", chat_type=ChatType.DM),
    text="continue",
    conversation=key,
    turn_id="turn-runtime-b",
)
print(json.dumps({
    "session_exists": state.session_exists,
    "last_turn_id": state.last_turn_id,
    "plan_action": state.plans[0].execution_action.value,
    "automatic_replay": list(state.automatic_replay_effect_ids),
    "new_turn_id": request.turn_id,
}))
'''
    result = subprocess.run(
        [sys.executable, "-c", reader, str(root)],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    evidence = json.loads(result.stdout)

    assert evidence == {
        "session_exists": True,
        "last_turn_id": "turn-runtime-a",
        "plan_action": "retry_allowed",
        "automatic_replay": [],
        "new_turn_id": "turn-runtime-b",
    }
