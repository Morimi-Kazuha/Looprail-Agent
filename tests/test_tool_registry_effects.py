from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from pico.agent.effects import (
    EffectClass,
    EffectJournal,
    EffectStatus,
    RecoveryAction,
    RecoveryPlanner,
    observe_local_write,
)
from pico.agent.tools.base import Tool, ToolResult
from pico.agent.tools.execution import ToolCapability, ToolEffect, ToolExecutionContext, ToolInvocation
from pico.agent.tools.registry import ToolRegistry


class _Crash(BaseException):
    pass


class _ReadProbe(Tool):
    capability = ToolCapability(effect=ToolEffect.READ)

    def __init__(self) -> None:
        self.calls = 0

    @property
    def name(self) -> str:
        return "read_probe"

    @property
    def description(self) -> str:
        return "deterministic read probe"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"value": {"type": "string"}}}

    async def execute(self, value: str = "ok", **kwargs: Any) -> str:
        self.calls += 1
        return value


class _WriteProbe(Tool):
    # The name is intentional: Phase 0A only narrows this exact existing
    # write_file capability to LOCAL_WRITE.
    capability = ToolCapability(effect=ToolEffect.WRITE)

    def __init__(self) -> None:
        self.calls = 0

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return "deterministic write probe"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        }

    async def execute(self, path: str, content: str, **kwargs: Any) -> str:
        self.calls += 1
        Path(path).write_text(content, encoding="utf-8")
        return "write complete"


class _FailingJournal:
    def append(self, record) -> None:
        raise OSError("journal unavailable")


class _FailingTerminalJournal:
    def __init__(self, journal: EffectJournal) -> None:
        self.journal = journal

    def append(self, record) -> None:
        if record.status in {EffectStatus.COMMITTED, EffectStatus.FAILED, EffectStatus.UNKNOWN}:
            raise OSError("terminal journal unavailable")
        self.journal.append(record)


class _ReadTimeoutProbe(Tool):
    capability = ToolCapability(effect=ToolEffect.READ)
    timeout_seconds = 0.05

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    @property
    def name(self) -> str:
        return "read_timeout_probe"

    @property
    def description(self) -> str:
        return "deterministic read timeout probe"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:
        self.started.set()
        await self.release.wait()
        return "late"


class _WriteThenTimeout(_WriteProbe):
    timeout_seconds = 0.05

    def __init__(self) -> None:
        super().__init__()
        self.release = asyncio.Event()

    async def execute(self, path: str, content: str, **kwargs: Any) -> str:
        self.calls += 1
        Path(path).write_text(content, encoding="utf-8")
        await self.release.wait()
        return "late"


class _OpaqueTimeoutProbe(Tool):
    timeout_seconds = 0.05

    def __init__(self, effect: ToolEffect) -> None:
        self.capability = ToolCapability(effect=effect)
        self.release = asyncio.Event()

    @property
    def name(self) -> str:
        return "opaque_timeout_probe"

    @property
    def description(self) -> str:
        return "deterministic opaque timeout probe"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:
        await self.release.wait()
        return "late"


class _CancellableExternalProbe(Tool):
    capability = ToolCapability(effect=ToolEffect.EXTERNAL)

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    @property
    def name(self) -> str:
        return "cancellable_external_probe"

    @property
    def description(self) -> str:
        return "deterministic cancellation probe"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:
        self.started.set()
        await self.release.wait()
        return "late"


def _context() -> ToolExecutionContext:
    return ToolExecutionContext(call_id="call-1", session_key="test:chat", turn_id="turn-1")


@pytest.mark.asyncio
async def test_prepared_is_durable_before_tool_and_crash_leaves_prepared(tmp_path) -> None:
    journal = EffectJournal(tmp_path)
    tool = _ReadProbe()

    def crash(stage, record) -> None:
        if stage == "prepared":
            raise _Crash("after prepared")

    registry = ToolRegistry(effect_journal=journal, crash_hook=crash)
    registry.register(tool)

    with pytest.raises(_Crash):
        await registry.execute_invocation(ToolInvocation(tool.name, {"value": "read"}, _context()))

    records = journal.load()
    assert [record.status for record in records] == [EffectStatus.PREPARED]
    assert records[0].effect_id.startswith("effect-")
    assert journal.latest(records[0].effect_id) == records[0]
    assert records[0].effect_class is EffectClass.READ
    assert records[0].turn_id == "turn-1"
    assert tool.calls == 0


@pytest.mark.asyncio
async def test_running_read_crash_is_retryable(tmp_path) -> None:
    journal = EffectJournal(tmp_path)
    tool = _ReadProbe()

    def crash(stage, record) -> None:
        if stage == "running":
            raise _Crash("after running")

    registry = ToolRegistry(effect_journal=journal, crash_hook=crash)
    registry.register(tool)

    with pytest.raises(_Crash):
        await registry.execute_invocation(ToolInvocation(tool.name, {}, _context()))

    latest = journal.load()[-1]
    decision = RecoveryPlanner.plan(latest)
    assert latest.status is EffectStatus.RUNNING
    assert decision.action is RecoveryAction.RETRY
    assert tool.calls == 0


@pytest.mark.asyncio
async def test_read_retry_can_commit_after_restart(tmp_path) -> None:
    journal = EffectJournal(tmp_path)
    first_tool = _ReadProbe()

    def crash(stage, record) -> None:
        if stage == "running":
            raise _Crash("simulated process loss")

    first_registry = ToolRegistry(effect_journal=journal, crash_hook=crash)
    first_registry.register(first_tool)
    with pytest.raises(_Crash):
        await first_registry.execute_invocation(ToolInvocation(first_tool.name, {}, _context()))
    assert RecoveryPlanner.plan(journal.load()[-1]).action is RecoveryAction.RETRY

    reloaded = EffectJournal(path=journal.path)
    retry_tool = _ReadProbe()
    retry_registry = ToolRegistry(effect_journal=reloaded)
    retry_registry.register(retry_tool)
    execution = await retry_registry.execute_invocation(ToolInvocation(retry_tool.name, {}, _context()))

    assert execution.effect_id is not None
    assert execution.invocation.context.effect_id == execution.effect_id
    assert execution.result == "ok"
    assert reloaded.latest(execution.effect_id).status is EffectStatus.COMMITTED
    assert retry_tool.calls == 1


@pytest.mark.asyncio
async def test_local_write_rechecks_existing_precondition_before_mutation(tmp_path) -> None:
    journal = EffectJournal(tmp_path)
    target = tmp_path / "result.txt"
    target.write_text("before", encoding="utf-8")
    tool = _WriteProbe()

    def third_party_change(stage, record) -> None:
        if stage == "running":
            target.write_text("third-party", encoding="utf-8")

    registry = ToolRegistry(effect_journal=journal, crash_hook=third_party_change)
    registry.register(tool)

    result = await registry.execute(
        tool.name,
        {"path": str(target), "content": "after"},
        context=_context(),
    )

    latest = journal.load()[-1]
    assert result.failed is True
    assert "precondition" in result.lower()
    assert target.read_text(encoding="utf-8") == "third-party"
    assert tool.calls == 0
    assert latest.status is EffectStatus.FAILED
    assert RecoveryPlanner.plan(latest, observe_local_write(latest)).action is RecoveryAction.ASK_HUMAN


@pytest.mark.asyncio
async def test_local_write_existing_unchanged_precondition_commits(tmp_path) -> None:
    journal = EffectJournal(tmp_path)
    target = tmp_path / "result.txt"
    target.write_text("before", encoding="utf-8")
    tool = _WriteProbe()
    registry = ToolRegistry(effect_journal=journal)
    registry.register(tool)

    execution = await registry.execute_invocation(
        ToolInvocation(tool.name, {"path": str(target), "content": "after"}, _context())
    )

    assert execution.result.failed is False
    assert target.read_text(encoding="utf-8") == "after"
    assert tool.calls == 1
    assert journal.latest(execution.effect_id).status is EffectStatus.COMMITTED


@pytest.mark.asyncio
async def test_local_write_nonexistent_unchanged_precondition_commits(tmp_path) -> None:
    journal = EffectJournal(tmp_path)
    target = tmp_path / "new-result.txt"
    tool = _WriteProbe()
    registry = ToolRegistry(effect_journal=journal)
    registry.register(tool)

    execution = await registry.execute_invocation(
        ToolInvocation(tool.name, {"path": str(target), "content": "created"}, _context())
    )

    assert execution.result.failed is False
    assert target.read_text(encoding="utf-8") == "created"
    assert tool.calls == 1
    assert journal.latest(execution.effect_id).status is EffectStatus.COMMITTED


@pytest.mark.asyncio
async def test_local_write_blocks_third_party_creation_of_missing_file(tmp_path) -> None:
    journal = EffectJournal(tmp_path)
    target = tmp_path / "result.txt"
    tool = _WriteProbe()

    def third_party_create(stage, record) -> None:
        if stage == "running":
            target.write_text("third-party", encoding="utf-8")

    registry = ToolRegistry(effect_journal=journal, crash_hook=third_party_create)
    registry.register(tool)

    result = await registry.execute(
        tool.name,
        {"path": str(target), "content": "after"},
        context=_context(),
    )

    latest = journal.load()[-1]
    assert result.failed is True
    assert target.read_text(encoding="utf-8") == "third-party"
    assert tool.calls == 0
    assert latest.status is EffectStatus.FAILED
    assert RecoveryPlanner.plan(latest, observe_local_write(latest)).action is RecoveryAction.ASK_HUMAN


@pytest.mark.asyncio
async def test_local_write_post_hash_allows_skip_without_second_execution(tmp_path) -> None:
    journal = EffectJournal(tmp_path)
    target = tmp_path / "result.txt"
    tool = _WriteProbe()

    def crash(stage, record) -> None:
        if stage == "before_terminal":
            raise _Crash("after local write, before commit")

    registry = ToolRegistry(effect_journal=journal, crash_hook=crash)
    registry.register(tool)
    invocation = ToolInvocation(
        tool.name,
        {"path": str(target), "content": "durable"},
        _context(),
    )

    with pytest.raises(_Crash):
        await registry.execute_invocation(invocation)

    latest = journal.load()[-1]
    decision = RecoveryPlanner.plan(latest, observe_local_write(latest))
    assert latest.status is EffectStatus.RUNNING
    assert decision.action is RecoveryAction.SKIP
    assert target.read_text(encoding="utf-8") == "durable"
    assert tool.calls == 1


@pytest.mark.asyncio
async def test_local_write_pre_hash_allows_retry_when_tool_never_started(tmp_path) -> None:
    journal = EffectJournal(tmp_path)
    target = tmp_path / "result.txt"
    target.write_text("before", encoding="utf-8")
    tool = _WriteProbe()

    def crash(stage, record) -> None:
        if stage == "running":
            raise _Crash("before local write")

    registry = ToolRegistry(effect_journal=journal, crash_hook=crash)
    registry.register(tool)
    with pytest.raises(_Crash):
        await registry.execute_invocation(
            ToolInvocation(tool.name, {"path": str(target), "content": "after"}, _context())
        )

    latest = journal.load()[-1]
    decision = RecoveryPlanner.plan(latest, observe_local_write(latest))
    assert decision.action is RecoveryAction.RETRY
    assert tool.calls == 0


@pytest.mark.asyncio
async def test_local_write_conflict_requires_human(tmp_path) -> None:
    journal = EffectJournal(tmp_path)
    target = tmp_path / "result.txt"
    target.write_text("before", encoding="utf-8")
    tool = _WriteProbe()

    def crash(stage, record) -> None:
        if stage == "running":
            raise _Crash("before local write")

    registry = ToolRegistry(effect_journal=journal, crash_hook=crash)
    registry.register(tool)
    with pytest.raises(_Crash):
        await registry.execute_invocation(
            ToolInvocation(tool.name, {"path": str(target), "content": "after"}, _context())
        )
    target.write_text("conflict", encoding="utf-8")

    latest = journal.load()[-1]
    assert RecoveryPlanner.plan(latest, observe_local_write(latest)).action is RecoveryAction.ASK_HUMAN


@pytest.mark.asyncio
async def test_failed_result_after_write_uses_postcondition_not_failed_flag(tmp_path) -> None:
    class _WriteThenFail(_WriteProbe):
        async def execute(self, path: str, content: str, **kwargs: Any) -> str:
            self.calls += 1
            Path(path).write_text(content, encoding="utf-8")
            return ToolResult("reported failure after write", failed=True)

    journal = EffectJournal(tmp_path)
    target = tmp_path / "result.txt"
    tool = _WriteThenFail()
    registry = ToolRegistry(effect_journal=journal)
    registry.register(tool)

    execution = await registry.execute_invocation(
        ToolInvocation(tool.name, {"path": str(target), "content": "written"}, _context())
    )

    assert execution.result.failed is True
    assert journal.latest(execution.effect_id).status is EffectStatus.COMMITTED


@pytest.mark.asyncio
async def test_local_write_exception_after_write_commits_from_postcondition(tmp_path) -> None:
    class _WriteThenRaise(_WriteProbe):
        async def execute(self, path: str, content: str, **kwargs: Any) -> str:
            self.calls += 1
            Path(path).write_text(content, encoding="utf-8")
            raise RuntimeError("reported after write")

    journal = EffectJournal(tmp_path)
    target = tmp_path / "result.txt"
    tool = _WriteThenRaise()
    registry = ToolRegistry(effect_journal=journal)
    registry.register(tool)

    execution = await registry.execute_invocation(
        ToolInvocation(tool.name, {"path": str(target), "content": "written"}, _context())
    )

    assert execution.result.failed is True
    assert target.read_text(encoding="utf-8") == "written"
    assert journal.latest(execution.effect_id).status is EffectStatus.COMMITTED


@pytest.mark.asyncio
async def test_local_write_timeout_commits_from_postcondition(tmp_path) -> None:
    journal = EffectJournal(tmp_path)
    target = tmp_path / "result.txt"
    tool = _WriteThenTimeout()
    registry = ToolRegistry(effect_journal=journal)
    registry.register(tool)

    execution = await registry.execute_invocation(
        ToolInvocation(tool.name, {"path": str(target), "content": "written"}, _context())
    )

    assert execution.result.failed is True
    assert "timed out" in execution.result
    assert target.read_text(encoding="utf-8") == "written"
    assert journal.latest(execution.effect_id).status is EffectStatus.COMMITTED


@pytest.mark.asyncio
async def test_read_timeout_is_failed_and_conservatively_retryable(tmp_path) -> None:
    journal = EffectJournal(tmp_path)
    tool = _ReadTimeoutProbe()
    registry = ToolRegistry(effect_journal=journal)
    registry.register(tool)

    execution = await registry.execute_invocation(ToolInvocation(tool.name, {}, _context()))

    latest = journal.latest(execution.effect_id)
    assert execution.result.failed is True
    assert latest.status is EffectStatus.FAILED
    assert RecoveryPlanner.plan(latest).action is RecoveryAction.RETRY


@pytest.mark.asyncio
@pytest.mark.parametrize("effect", [ToolEffect.EXTERNAL, ToolEffect.EXECUTE, ToolEffect.UNKNOWN])
async def test_opaque_timeout_is_unknown_and_asks_human(tmp_path, effect) -> None:
    journal = EffectJournal(tmp_path)
    tool = _OpaqueTimeoutProbe(effect)
    registry = ToolRegistry(effect_journal=journal)
    registry.register(tool)

    execution = await registry.execute_invocation(ToolInvocation(tool.name, {}, _context()))

    latest = journal.latest(execution.effect_id)
    assert execution.result.failed is True
    assert latest.status is EffectStatus.UNKNOWN
    assert RecoveryPlanner.plan(latest).action is RecoveryAction.ASK_HUMAN


@pytest.mark.asyncio
async def test_external_cancellation_is_unknown_and_not_auto_replayed(tmp_path) -> None:
    journal = EffectJournal(tmp_path)
    tool = _CancellableExternalProbe()
    registry = ToolRegistry(effect_journal=journal)
    registry.register(tool)
    task = asyncio.create_task(registry.execute_invocation(ToolInvocation(tool.name, {}, _context())))

    await tool.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    latest = journal.load()[-1]
    assert latest.effect_class is EffectClass.EXTERNAL
    assert latest.status is EffectStatus.UNKNOWN
    assert RecoveryPlanner.plan(latest).action is RecoveryAction.ASK_HUMAN


@pytest.mark.asyncio
async def test_terminal_journal_failure_leaves_running_and_reports_unknown(tmp_path) -> None:
    durable_journal = EffectJournal(tmp_path)
    journal = _FailingTerminalJournal(durable_journal)
    tool = _ReadProbe()
    registry = ToolRegistry(effect_journal=journal)
    registry.register(tool)

    execution = await registry.execute_invocation(ToolInvocation(tool.name, {}, _context()))

    latest = durable_journal.latest(execution.effect_id)
    assert execution.result.failed is True
    assert "outcome is unknown" in execution.result
    assert latest.status is EffectStatus.RUNNING


@pytest.mark.asyncio
async def test_committed_effect_survives_without_session_save(tmp_path) -> None:
    journal = EffectJournal(tmp_path)
    target = tmp_path / "result.txt"
    tool = _WriteProbe()
    registry = ToolRegistry(effect_journal=journal)
    registry.register(tool)

    execution = await registry.execute_invocation(
        ToolInvocation(tool.name, {"path": str(target), "content": "committed"}, _context())
    )
    reloaded = EffectJournal(path=journal.path)

    assert reloaded.latest(execution.effect_id).status is EffectStatus.COMMITTED
    assert RecoveryPlanner.plan(reloaded.latest(execution.effect_id)).action is RecoveryAction.SKIP


@pytest.mark.asyncio
async def test_external_failure_is_unknown_and_never_auto_replayed(tmp_path) -> None:
    class _ExternalProbe(Tool):
        capability = ToolCapability(effect=ToolEffect.EXTERNAL)

        @property
        def name(self) -> str:
            return "external_probe"

        @property
        def description(self) -> str:
            return "deterministic external probe"

        @property
        def parameters(self) -> dict[str, Any]:
            return {"type": "object", "properties": {}}

        async def execute(self, **kwargs: Any) -> str:
            return ToolResult("external result unavailable", failed=True)

    journal = EffectJournal(tmp_path)
    registry = ToolRegistry(effect_journal=journal)
    registry.register(_ExternalProbe())

    execution = await registry.execute_invocation(ToolInvocation("external_probe", {}, _context()))
    latest = journal.latest(execution.effect_id)

    assert latest.effect_class is EffectClass.EXTERNAL
    assert latest.status is EffectStatus.UNKNOWN
    assert RecoveryPlanner.plan(latest).action is RecoveryAction.ASK_HUMAN


@pytest.mark.asyncio
async def test_journal_failure_fail_closes_side_effect_tool(tmp_path) -> None:
    tool = _WriteProbe()
    registry = ToolRegistry(effect_journal=_FailingJournal())
    registry.register(tool)

    result = await registry.execute(
        tool.name,
        {"path": str(tmp_path / "must-not-exist"), "content": "blocked"},
        context=_context(),
    )

    assert result.failed is True
    assert tool.calls == 0
    assert not (tmp_path / "must-not-exist").exists()
