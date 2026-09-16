"""Focused Phase 2 contract tests for the shipping ToolRegistry boundary.

These tests deliberately exercise the existing Registry rather than introducing a
parallel runtime abstraction.  They record the supported Medium+ contract at the
resolution, validation, effect, timeout, cancellation, filesystem, shell, and
result boundaries.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from pico.agent.effects import EffectClass, EffectJournal, EffectStatus
from pico.agent.tools.base import Tool, ToolResult
from pico.agent.tools.execution import ToolCapability, ToolEffect, ToolExecutionContext
from pico.agent.tools.filesystem import ReadFileTool, WriteFileTool
from pico.agent.tools.registry import ToolRegistry
from pico.agent.tools.shell import ExecTool
from pico.sandbox import ExecResult, SandboxExecutor


def _context() -> ToolExecutionContext:
    return ToolExecutionContext(call_id="call-phase2", session_key="session-phase2", turn_id="turn-phase2")


def _latest(journal: EffectJournal, result: ToolResult):
    assert result.effect_id
    record = journal.latest(result.effect_id)
    assert record is not None
    return record


class _RequiredProbe(Tool):
    capability = ToolCapability(effect=ToolEffect.READ)

    def __init__(self) -> None:
        self.calls = 0

    @property
    def name(self) -> str:
        return "required_probe"

    @property
    def description(self) -> str:
        return "A deterministic validation probe."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "value": {"type": "string"},
                "count": {"type": "integer"},
            },
            "required": ["value"],
        }

    async def execute(self, value: str, count: int | None = None, **kwargs: Any) -> str:
        self.calls += 1
        return f"{value}:{count}"


class _ReturnProbe(Tool):
    capability = ToolCapability(effect=ToolEffect.READ)

    def __init__(self, value: Any) -> None:
        self.value = value

    @property
    def name(self) -> str:
        return "return_probe"

    @property
    def description(self) -> str:
        return "A deterministic result-normalization probe."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> Any:
        return self.value


class _ExceptionProbe(Tool):
    capability = ToolCapability(effect=ToolEffect.READ)

    @property
    def name(self) -> str:
        return "exception_probe"

    @property
    def description(self) -> str:
        return "A deterministic exception probe."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:
        raise RuntimeError("probe failure")


class _WaitProbe(Tool):
    def __init__(self, effect: ToolEffect, timeout_seconds: float | None = None) -> None:
        self.capability = ToolCapability(effect=effect)
        self.timeout_seconds = timeout_seconds
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    @property
    def name(self) -> str:
        return "wait_probe"

    @property
    def description(self) -> str:
        return "A deterministic timeout/cancellation probe."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:
        self.started.set()
        await self.release.wait()
        return "released"


class _NonZeroExecutor(SandboxExecutor):
    """Deterministic host-style executor for shell exit-code normalization."""

    @property
    def is_sandboxed(self) -> bool:
        return False

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        timeout: int | None = None,
        env: dict[str, str] | None = None,
    ) -> ExecResult:
        return ExecResult(stdout="stdout", stderr="stderr", exit_code=7)


class _StableObject:
    def __str__(self) -> str:
        return "stable-object"


@pytest.mark.asyncio
async def test_unknown_tool_fails_before_effect_creation(tmp_path: Path) -> None:
    journal = EffectJournal(tmp_path)
    registry = ToolRegistry(effect_journal=journal)

    result = await registry.execute("missing_tool", {}, context=_context())

    assert isinstance(result, ToolResult)
    assert result.failed is True
    assert "not found" in result
    assert journal.load() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params",
    [
        {},
        {"value": "ok", "count": "not-an-integer"},
        ["malformed-payload"],
        None,
    ],
)
async def test_invalid_arguments_are_rejected_before_tool_execution(params: Any) -> None:
    tool = _RequiredProbe()
    registry = ToolRegistry()
    registry.register(tool)

    result = await registry.execute(tool.name, params, context=_context())

    assert result.failed is True
    assert "Invalid parameters" in result
    assert tool.calls == 0


@pytest.mark.asyncio
async def test_successful_read_has_committed_read_effect(tmp_path: Path) -> None:
    path = tmp_path / "read.txt"
    path.write_text("hello runtime\n", encoding="utf-8")
    journal = EffectJournal(tmp_path)
    registry = ToolRegistry(effect_journal=journal)
    registry.register(ReadFileTool(workspace=tmp_path, allowed_dir=tmp_path))

    result = await registry.execute("read_file", {"path": "read.txt"}, context=_context())

    assert result.failed is False
    assert "hello runtime" in result
    record = _latest(journal, result)
    assert record.effect_class is EffectClass.READ
    assert record.status is EffectStatus.COMMITTED
    assert record.session_key == "session-phase2"
    assert record.turn_id == "turn-phase2"


@pytest.mark.asyncio
async def test_successful_mutation_has_committed_local_write_effect(tmp_path: Path) -> None:
    journal = EffectJournal(tmp_path)
    registry = ToolRegistry(effect_journal=journal)
    registry.register(WriteFileTool(workspace=tmp_path, allowed_dir=tmp_path))

    result = await registry.execute(
        "write_file",
        {"path": "created.txt", "content": "created by runtime"},
        context=_context(),
    )

    assert result.failed is False
    assert (tmp_path / "created.txt").read_text(encoding="utf-8") == "created by runtime"
    record = _latest(journal, result)
    assert record.effect_class is EffectClass.LOCAL_WRITE
    assert record.status is EffectStatus.COMMITTED


@pytest.mark.asyncio
async def test_tool_exception_is_normalized_and_read_effect_fails(tmp_path: Path) -> None:
    journal = EffectJournal(tmp_path)
    registry = ToolRegistry(effect_journal=journal)
    registry.register(_ExceptionProbe())

    result = await registry.execute("exception_probe", {}, context=_context())

    assert result.failed is True
    assert "Error executing exception_probe" in result
    assert "probe failure" in result
    record = _latest(journal, result)
    assert record.effect_class is EffectClass.READ
    assert record.status is EffectStatus.FAILED
    assert record.error_class == "RuntimeError"


@pytest.mark.asyncio
async def test_timeout_returns_failure_and_finalizes_read_effect(tmp_path: Path) -> None:
    journal = EffectJournal(tmp_path)
    tool = _WaitProbe(ToolEffect.READ, timeout_seconds=0.02)
    registry = ToolRegistry(effect_journal=journal)
    registry.register(tool)

    result = await registry.execute(tool.name, {}, context=_context())

    assert result.failed is True
    assert "timed out" in result
    record = _latest(journal, result)
    assert record.effect_class is EffectClass.READ
    assert record.status is EffectStatus.FAILED
    assert record.error_class == "TimeoutError"
    assert tool.release.is_set() is False


@pytest.mark.asyncio
async def test_cancellation_during_external_effect_is_recorded_as_unknown(tmp_path: Path) -> None:
    journal = EffectJournal(tmp_path)
    tool = _WaitProbe(ToolEffect.EXTERNAL, timeout_seconds=5.0)
    registry = ToolRegistry(effect_journal=journal)
    registry.register(tool)

    task = asyncio.create_task(registry.execute(tool.name, {}, context=_context()))
    await asyncio.wait_for(tool.started.wait(), timeout=1.0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    records = journal.load()
    assert [record.status for record in records] == [
        EffectStatus.PREPARED,
        EffectStatus.RUNNING,
        EffectStatus.UNKNOWN,
    ]
    assert records[-1].effect_class is EffectClass.EXTERNAL
    assert records[-1].error_class == "CancelledError"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["../outside.txt", "C:/outside.txt"])
async def test_filesystem_escape_is_rejected_and_never_committed(tmp_path: Path, path: str) -> None:
    journal = EffectJournal(tmp_path)
    registry = ToolRegistry(effect_journal=journal)
    registry.register(ReadFileTool(workspace=tmp_path, allowed_dir=tmp_path))

    result = await registry.execute("read_file", {"path": path}, context=_context())

    assert result.failed is True
    assert "outside allowed directory" in result or "outside" in result
    record = _latest(journal, result)
    assert record.effect_class is EffectClass.READ
    assert record.status is EffectStatus.FAILED


@pytest.mark.asyncio
async def test_shell_nonzero_exit_is_failed_execute_effect(tmp_path: Path) -> None:
    tool = ExecTool(
        working_dir=str(tmp_path),
        restrict_to_workspace=True,
        executor=_NonZeroExecutor(),
    )
    journal = EffectJournal(tmp_path)
    registry = ToolRegistry(effect_journal=journal)
    registry.register(tool)

    result = await registry.execute("exec", {"command": "deterministic-failure"}, context=_context())

    assert result.failed is True
    assert "STDERR:" in result
    assert "Exit code: 7" in result
    record = _latest(journal, result)
    assert record.effect_class is EffectClass.EXECUTE
    assert record.status is EffectStatus.UNKNOWN


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["plain text", {"ok": True}, "", None, _StableObject()])
async def test_registry_result_boundary_is_toolresult_string(value: Any) -> None:
    tool = _ReturnProbe(value)
    registry = ToolRegistry()
    registry.register(tool)

    result = await registry.execute(tool.name, {})

    assert isinstance(result, ToolResult)
    assert isinstance(result, str)
    assert result.failed is False
    assert str(result) == str(value)


@pytest.mark.asyncio
async def test_successful_terminal_effect_has_prepared_running_committed_history(tmp_path: Path) -> None:
    journal = EffectJournal(tmp_path)
    registry = ToolRegistry(effect_journal=journal)
    registry.register(_RequiredProbe())

    result = await registry.execute("required_probe", {"value": "ok"}, context=_context())

    history = journal.history(result.effect_id)
    assert [record.status for record in history] == [
        EffectStatus.PREPARED,
        EffectStatus.RUNNING,
        EffectStatus.COMMITTED,
    ]
    assert len({record.effect_id for record in history}) == 1
