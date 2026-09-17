"""Contracts for the reviewer-facing ``looprail run`` surface.

The happy-path case deliberately keeps the CLI, runtime assembly, Scheduler,
AgentLoop, ToolRegistry and TraceStore real.  Only the Provider is scripted so
that the fixture remains local, deterministic and free of paid/network calls.
The smaller cases pin the observation boundary: terminal state comes from
Spine lifecycle facts, tool effects come from Runtime receipts, and recovery is
shown as a new Turn rather than a process replay.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from looprail.agent.recovery import RecoveryProjector, RecoveryState
from looprail.agent.recovery.state import RecoveryStateStore
from looprail.cli._run_surface import (
    CliRunReporter,
    RunEvidence,
    RunVerbosity,
    format_tool_complete,
    parse_verbosity,
    terminal_status,
)
from looprail.cli.commands import app
from looprail.config.loader import save_config, set_config_path
from looprail.config.schema import Config
from looprail.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from looprail.spine import TurnOutcome, Usage
from looprail.spine.events import ToolEvent, ToolPhase, TurnEnded, TurnFailed
from looprail.tracing.store import TraceStore

runner = CliRunner()


class _DeterministicDemoProvider(LLMProvider):
    """Drive a complete local coding task through real Tool Runtime calls."""

    def __init__(self) -> None:
        super().__init__(api_key="phase7-fixture")
        self.calls: list[str] = []
        self._responses = [
            ToolCallRequest(
                id="search-1",
                name="grep",
                arguments={
                    "pattern": "return left - right",
                    "path": "calculator.py",
                    "output_mode": "content",
                },
            ),
            ToolCallRequest(
                id="read-1",
                name="read_file",
                arguments={"path": "calculator.py"},
            ),
            ToolCallRequest(
                id="write-1",
                name="write_file",
                arguments={
                    "path": "calculator.py",
                    "content": (
                        "def add(left: int, right: int) -> int:\n"
                        "    \"\"\"Return the sum of two integers.\"\"\"\n"
                        "\n"
                        "    return left + right\n"
                    ),
                },
            ),
            ToolCallRequest(
                id="test-1",
                name="exec",
                arguments={"command": "python -m pytest -q test_calculator.py"},
            ),
        ]

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        del tools, kwargs
        self.calls.append(str(messages[-1].get("role", "unknown")) if messages else "empty")
        index = len(self.calls) - 1
        if index < len(self._responses):
            return LLMResponse(
                content=None,
                tool_calls=[self._responses[index]],
                finish_reason="tool_calls",
                usage={"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
                model=model or self.get_default_model(),
            )
        return LLMResponse(
            content="Fixed calculator.py and verified the focused test.",
            finish_reason="stop",
            usage={"prompt_tokens": 18, "completion_tokens": 8, "total_tokens": 26},
            model=model or self.get_default_model(),
        )

    def get_default_model(self) -> str:
        return "fixture/phase7-deterministic"


class _ToolValidationFailureProvider(_DeterministicDemoProvider):
    """Return one invalid call, then a final answer for the failure demo."""

    def __init__(self) -> None:
        super().__init__()
        self._responses = [
            ToolCallRequest(id="invalid-1", name="read_file", arguments={}),
        ]


class _MaxIterationProvider(LLMProvider):
    """Force a real AgentLoop recovery marker, then answer on resume."""

    def __init__(self) -> None:
        super().__init__(api_key="phase7-recovery")
        self.calls = 0

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        del messages, tools, kwargs
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCallRequest(
                        id="recovery-search-1",
                        name="grep",
                        arguments={"pattern": "return left - right", "path": "calculator.py"},
                    )
                ],
                finish_reason="tool_calls",
                usage={"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10},
                model=model or self.get_default_model(),
            )
        return LLMResponse(
            content="The interrupted run is resumable; this is a new provider turn.",
            finish_reason="stop",
            usage={"prompt_tokens": 10, "completion_tokens": 9, "total_tokens": 19},
            model=model or self.get_default_model(),
        )

    def get_default_model(self) -> str:
        return "fixture/phase7-recovery"


def _write_cli_config(path: Path) -> None:
    config = Config()
    config.agents.defaults.model = "fixture/phase7-deterministic"
    config.agents.defaults.provider = "custom"
    config.agents.defaults.max_tool_iterations = 8
    config.providers.custom.api_key = "phase7-fixture-key"
    config.channels.send_progress = True
    config.channels.send_tool_hints = True
    config.tools.restrict_to_workspace = True
    config.tools.sandbox.backend = "none"
    save_config(config, path)

    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.update(
        {
            "memory": {"backend": None},
            "plugins": {"disabled": []},
            "skillForge": {"enabled": False, "router": {"enabled": False}},
            "tracing": {"enabled": True},
        }
    )
    path.write_text(json.dumps(raw, indent=2), encoding="utf-8")


@pytest.fixture
def cli_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    workspace = tmp_path / "workspace"
    source = Path(__file__).parent / "fixtures" / "cli_repair_demo"
    shutil.copytree(source, workspace)
    config_path = tmp_path / "config.json"
    trace_root = tmp_path / "trace-root"
    _write_cli_config(config_path)
    set_config_path(config_path)
    monkeypatch.setenv("LOOPRAIL_TRACING", "1")
    monkeypatch.setenv("LOOPRAIL_TRACING_DIR", str(trace_root))
    # DirectExecutor deliberately passes a small environment allowlist to the
    # child shell.  Put the test runner's Python first so the fixture's
    # ``python -m pytest`` command uses the same deterministic environment as
    # the acceptance process on Windows.
    python_bin = str(Path(sys.executable).parent)
    monkeypatch.setenv("PATH", python_bin + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("VIRTUAL_ENV", str(Path(sys.executable).parent.parent))
    from looprail.tracing import spans

    monkeypatch.setattr(spans, "_store", None)
    yield workspace, config_path, trace_root
    set_config_path(None)  # type: ignore[arg-type]


def _invoke_happy_cli(
    monkeypatch: pytest.MonkeyPatch,
    workspace: Path,
    config_path: Path,
    provider: _DeterministicDemoProvider,
    *extra: str,
):
    monkeypatch.setattr("looprail.cli.agent_commands.make_provider", lambda _config: provider)
    return runner.invoke(
        app,
        [
            "run",
            "--message",
            "Find and fix the calculator bug, then run its test.",
            "--workspace",
            str(workspace),
            "--config",
            str(config_path),
            *extra,
        ],
    )


def test_cli_verbosity_is_small_and_bounded() -> None:
    assert parse_verbosity(" NORMAL ") is RunVerbosity.NORMAL
    assert parse_verbosity("verbose") is RunVerbosity.VERBOSE
    assert parse_verbosity("QUIET") is RunVerbosity.QUIET
    with pytest.raises(ValueError, match="normal, verbose, quiet"):
        parse_verbosity("json")

    event = ToolEvent(
        phase=ToolPhase.COMPLETE,
        tool_call_id="call-1",
        name="write_file",
        result_preview="the model claims it succeeded",
        effect_id="effect-unknown",
        effect_status="unknown",
        duration_ms=4.8,
    )
    rendered = format_tool_complete(event, start_arguments={"path": "calculator.py"}, verbose=True)
    assert "? effect outcome unknown" in rendered
    assert "effect_id: effect-unknown" in rendered
    assert "OK committed" not in rendered


def test_cli_terminal_status_uses_lifecycle_not_text() -> None:
    outcome = TurnOutcome(usage=Usage(0, 0, 0), explicit_reply=True)
    ended = TurnEnded(usage=Usage(0, 0, 0), latency_ms=1.0, explicit_reply=True)
    failed = TurnFailed(error="provider offline", cancelled=False)
    cancelled = TurnFailed(error="cancelled", cancelled=True)

    assert terminal_status(ended, outcome) == "COMPLETED"
    assert terminal_status(failed, None) == "FAILED"
    assert terminal_status(cancelled, None) == "CANCELLED"
    assert terminal_status(None, outcome) == "COMPLETED"
    budget_evidence = RunEvidence(
        summary={
            "spans": [
                {
                    "name": "spine.turn",
                    "attributes": {"spine.failure_category": "context_budget_failure"},
                }
            ]
        }
    )
    assert terminal_status(failed, None, evidence=budget_evidence) == "BUDGET_EXCEEDED"


def test_cli_reporter_renders_bounded_summary_from_durable_records(tmp_path: Path) -> None:
    lines: list[str] = []
    evidence = RunEvidence(
        run_id="run-demo",
        trace_path=str(tmp_path / "run-demo.jsonl"),
        summary={"span_count": 3, "spans": []},
        records=(
            {
                "name": "context.assemble",
                "attributes": {
                    "context.estimated_tokens_after": 240,
                    "context.limit": 4096,
                    "context.items_compacted": 2,
                    "context.items_dropped": 1,
                    "context.input_budget": 3000,
                },
            },
            {
                "name": "llm.call",
                "attributes": {"llm.provider": "fixture", "llm.model": "fixture/model"},
            },
        ),
    )
    reporter = CliRunReporter(lines.append, "cli:demo", "bounded task", verbosity=RunVerbosity.VERBOSE)
    reporter.turn_id = "turn-demo"
    # Keep the lookup seam deterministic; this test is about rendering fields,
    # while the actual CLI test below covers TraceStore discovery.
    import looprail.cli._run_surface as surface

    original = surface.discover_run_evidence
    surface.discover_run_evidence = lambda _turn, _state: evidence
    try:
        status, returned = reporter.finish(outcome=TurnOutcome(usage=Usage(0, 0, 0), explicit_reply=True))
    finally:
        surface.discover_run_evidence = original

    assert status == "COMPLETED"
    assert returned is evidence
    joined = "\n".join(lines)
    assert "estimated: 240" in joined
    assert "compacted: 2" in joined
    assert "dropped: 1" in joined
    assert "input budget: 3000" in joined
    assert "fixture/model" in joined


def test_cli_recovery_unknown_effect_is_visible_and_not_replayed() -> None:
    lines: list[str] = []
    state = RecoveryState(
        session_key="cli:recovery",
        artifact_status="available",
        last_turn_id="turn-old",
        last_turn_status="interrupted",
        effect_journal_status="available",
        unknown_effect_ids=("effect-unknown",),
    )
    reporter = CliRunReporter(lines.append, state.session_key, "resume", recovery_state=state)
    reporter.render_recovery()
    joined = "\n".join(lines)
    assert "unknown effects: effect-unknown" in joined
    assert "automatic replay: disabled" in joined


def test_cli_happy_path_uses_real_runtime_and_trace(
    cli_fixture: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, config_path, trace_root = cli_fixture
    provider = _DeterministicDemoProvider()
    result = _invoke_happy_cli(monkeypatch, workspace, config_path, provider, "--verbosity", "verbose")

    assert result.exit_code == 0, result.stdout
    assert provider.calls
    assert "LOOPRAIL Run" in result.stdout
    assert "Task" in result.stdout
    assert "grep" in result.stdout
    assert "read_file" in result.stdout
    assert "write_file" in result.stdout
    assert "exec python -m pytest -q test_calculator.py" in result.stdout
    assert "OK committed" in result.stdout
    assert "Result" in result.stdout and "COMPLETED" in result.stdout
    assert "Evidence" in result.stdout and "run: " in result.stdout
    assert "\x1b[" not in result.stdout
    assert "return left + right" in (workspace / "calculator.py").read_text(encoding="utf-8")

    match = re.search(r"Evidence\s+run: ([^\s]+)", result.stdout)
    assert match is not None
    run_id = match.group(1)
    records = TraceStore(trace_root).read_trace(run_id)
    names = {record.get("name") for record in records}
    assert "spine.turn" in names
    assert "context.assemble" in names
    assert "llm.call" in names
    assert "tool.call" in names
    assert any(record.get("attributes", {}).get("spine.outcome") == "completed" for record in records)


def test_cli_tool_validation_failure_is_explicit(
    cli_fixture: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, config_path, trace_root = cli_fixture
    provider = _ToolValidationFailureProvider()
    result = _invoke_happy_cli(monkeypatch, workspace, config_path, provider)

    assert result.exit_code == 0, result.stdout
    assert "FAIL tool_validation_failure" in result.stdout
    assert "COMPLETED_WITH_TOOL_FAILURE" in result.stdout
    assert "OK committed" not in result.stdout
    match = re.search(r"Evidence\s+run: ([^\s]+)", result.stdout)
    assert match is not None
    records = TraceStore(trace_root).read_trace(match.group(1))
    assert any(
        record.get("name") == "tool.call"
        and record.get("attributes", {}).get("tool.failure_category") == "tool_validation_failure"
        for record in records
    )


def test_cli_failure_keeps_trace_and_nonzero_result(
    cli_fixture: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, config_path, _trace_root = cli_fixture
    class _Subagents:
        def set_submit(self, _submit) -> None:
            pass

    class _FailingLoop:
        def __init__(self, **kwargs: Any) -> None:
            self.channels_config = kwargs.get("channels_config")
            self.subagents = _Subagents()

        def configure_personalization(self, _enabled: bool) -> None:
            pass

        async def run_turn(self, *_args: Any, **_kwargs: Any):
            raise RuntimeError("provider offline")

        def begin_close(self) -> None:
            pass

        async def close(self) -> None:
            pass

    monkeypatch.setattr("looprail.cli.agent_commands.make_provider", lambda _config: object())
    monkeypatch.setattr("looprail.agent.loop.AgentLoop", _FailingLoop)
    result = runner.invoke(
        app,
        [
            "run",
            "-m",
            "demonstrate provider failure",
            "-w",
            str(workspace),
            "--config",
            str(config_path),
        ],
    )

    assert result.exit_code == 1
    assert "Failure" in result.stdout
    assert "provider offline" in result.stdout
    assert "Result" in result.stdout and "FAILED" in result.stdout
    assert "Evidence" in result.stdout


def test_cli_resume_is_a_fresh_turn_with_durable_read(
    cli_fixture: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, config_path, _trace_root = cli_fixture
    from looprail.session.manager import SessionManager

    session_key = "cli:20990101_000000_phase7r"
    manager = SessionManager(workspace)
    session = manager.get_or_create(session_key)
    session.add_message("user", "the previous process was interrupted")
    manager.save(session)
    RecoveryStateStore(workspace).save_turn(
        session_key=session_key,
        turn_id="turn-previous",
        status="interrupted",
        checkpoint_id="checkpoint-reference",
        checkpoint_files=("calculator.py",),
    )

    class _Subagents:
        def set_submit(self, _submit) -> None:
            pass

    class _ResumeLoop:
        def __init__(self, **kwargs: Any) -> None:
            self.channels_config = kwargs.get("channels_config")
            self.subagents = _Subagents()
            self.state = kwargs["state"]
            self.sessions = kwargs["session_manager"]
            self.turn_ids: list[str | None] = []

        def configure_personalization(self, _enabled: bool) -> None:
            pass

        def prepare_resume(self, key: str):
            return RecoveryProjector(self.state, session_manager=self.sessions).project(key)

        async def run_turn(self, req, emit, _drain, **_kwargs):
            self.turn_ids.append(req.turn_id)
            await emit(
                __import__("looprail.spine.events", fromlist=["Text"]).Text(
                    content="continued from a fresh turn",
                    source=req.source,
                )
            )
            return TurnOutcome(usage=Usage(1, 1, 2), explicit_reply=True)

        def begin_close(self) -> None:
            pass

        async def close(self) -> None:
            pass

    loop_holder: list[_ResumeLoop] = []

    def _make_loop(**kwargs: Any):
        loop = _ResumeLoop(**kwargs)
        loop_holder.append(loop)
        return loop

    monkeypatch.setattr("looprail.cli.agent_commands.make_provider", lambda _config: object())
    monkeypatch.setattr("looprail.agent.loop.AgentLoop", _make_loop)
    result = runner.invoke(
        app,
        [
            "run",
            "-m",
            "continue the task",
            "--resume",
            session_key.removeprefix("cli:"),
            "-w",
            str(workspace),
            "--config",
            str(config_path),
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "Recovery" in result.stdout
    assert "previous Turn: turn-previous (interrupted)" in result.stdout
    assert "next Turn: NEW (fresh invocation)" in result.stdout
    assert "automatic replay: disabled" in result.stdout
    assert "continued from a fresh turn" in result.stdout
    assert loop_holder and loop_holder[0].turn_ids and loop_holder[0].turn_ids[0]
    assert loop_holder[0].turn_ids[0] != "turn-previous"


def test_real_cli_boundary_creates_marker_then_resumes(
    cli_fixture: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, config_path, _trace_root = cli_fixture
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["agents"]["defaults"]["maxToolIterations"] = 1
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    provider = _MaxIterationProvider()
    monkeypatch.setattr("looprail.cli.agent_commands.make_provider", lambda _config: provider)
    session_key = "cli:20990101_000000_phase7a"

    first = runner.invoke(
        app,
        [
            "run",
            "-m",
            "inspect the bug and continue if interrupted",
            "--session",
            session_key,
            "-w",
            str(workspace),
            "--config",
            str(config_path),
        ],
    )
    assert first.exit_code == 0, first.stdout
    assert "MAX_ITERATIONS" in first.stdout
    assert "Evidence" in first.stdout

    second = runner.invoke(
        app,
        [
            "run",
            "-m",
            "continue the interrupted task",
            "--resume",
            session_key.removeprefix("cli:"),
            "-w",
            str(workspace),
            "--config",
            str(config_path),
        ],
    )
    assert second.exit_code == 0, second.stdout
    assert "Recovery" in second.stdout
    assert "previous Turn:" in second.stdout
    assert "next Turn: NEW (fresh invocation)" in second.stdout
    assert "The interrupted run is resumable" in second.stdout
    assert "COMPLETED" in second.stdout
    assert provider.calls >= 3  # initial Tool call, exhaustion synthesis, resumed Turn


def test_cli_quiet_output_is_redirect_safe(
    cli_fixture: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, config_path, _trace_root = cli_fixture
    provider = _DeterministicDemoProvider()
    result = _invoke_happy_cli(monkeypatch, workspace, config_path, provider, "--verbosity", "quiet")

    assert result.exit_code == 0, result.stdout
    assert "Result" in result.stdout and "COMPLETED" in result.stdout
    assert "Evidence" in result.stdout
    assert "LOOPRAIL Run" not in result.stdout
    assert "Tool" not in result.stdout
    assert "\x1b" not in result.stdout
