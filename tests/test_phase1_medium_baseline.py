"""Deterministic CLI acceptance smoke for the Medium+ runtime baseline.

This test deliberately enters through the same assembly and REPL spine used by
``pico run -m``.  Only the provider is scripted; the Scheduler, AgentLoop,
ContextAssembler, ToolRegistry, filesystem tool, SessionManager, effect journal
and shutdown path remain real.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from pico.cli._repl_spine import build_repl
from pico.cli._runtime_assembly import assemble_runtime
from pico.cli.commands import app
from pico.config.pico import PicoConfig
from pico.config.schema import Config
from pico.providers.base import LLMResponse, ToolCallRequest
from pico.spine import ChatType, Origin, Source, TurnRequest

_runner = CliRunner()


class _BaselineProvider:
    """Return one deterministic file-tool call followed by a final answer."""

    def __init__(self) -> None:
        self.calls = 0
        self.tool_schemas: list[set[str]] = []

    def get_default_model(self) -> str:
        return "phase1/scripted"

    @staticmethod
    def _schema_names(tools: list[dict[str, Any]] | None) -> set[str]:
        return {
            str(tool["function"]["name"])
            for tool in tools or []
            if isinstance(tool, dict) and isinstance(tool.get("function"), dict)
        }

    async def chat_with_retry(self, **kwargs: Any) -> LLMResponse:
        self.tool_schemas.append(self._schema_names(kwargs.get("tools")))
        if self.calls == 0:
            response = LLMResponse(
                content=None,
                tool_calls=[
                    ToolCallRequest(
                        id="phase1-read-1",
                        name="read_file",
                        arguments={"path": "phase1-sentinel.txt"},
                    )
                ],
                finish_reason="tool_calls",
                usage={"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
            )
        else:
            response = LLMResponse(
                content="PHASE1_RUNTIME_OK",
                finish_reason="stop",
                usage={"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
            )
        self.calls += 1
        return response


@pytest.mark.medium_baseline
def test_medium_baseline_pico_run_invocation_is_deterministic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invoke the actual Typer ``pico run -m`` command with only its provider scripted."""
    from pico.config.loader import save_config, set_config_path

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "phase1-sentinel.txt").write_text("PHASE1_SENTINEL", encoding="utf-8")
    config_path = tmp_path / "config.json"
    set_config_path(config_path)
    provider = _BaselineProvider()
    monkeypatch.setattr("pico.cli.agent_commands.make_provider", lambda _config: provider)

    try:
        save_config(Config())
        raw_config = json.loads(config_path.read_text(encoding="utf-8"))
        raw_config["memory"] = {"backend": None}
        config_path.write_text(json.dumps(raw_config), encoding="utf-8")
        result = _runner.invoke(
            app,
            [
                "run",
                "-m",
                "Read phase1-sentinel.txt and return the deterministic marker.",
                "--workspace",
                str(workspace),
                "--config",
                str(config_path),
                "--no-markdown",
            ],
        )
    finally:
        set_config_path(None)  # type: ignore[arg-type]

    assert result.exit_code == 0, result.stdout
    assert "PHASE1_RUNTIME_OK" in result.stdout
    assert provider.calls == 2


@pytest.mark.medium_baseline
@pytest.mark.asyncio
async def test_medium_baseline_cli_run_uses_real_runtime_spine(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "phase1-sentinel.txt").write_text("PHASE1_SENTINEL", encoding="utf-8")

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    config.agents.defaults.model = "phase1/scripted"
    config.agents.defaults.max_tool_iterations = 2
    config.tools.restrict_to_workspace = True

    pico_config = PicoConfig(base=config)
    pico_config.memory.backend = None
    pico_config.skill_forge.enabled = False
    pico_config.skill_forge.router.enabled = False
    pico_config.skill_forge.rewrite_enabled = False
    pico_config.skill_forge.llm_gate_enabled = False
    pico_config.runtime.checkpoint.policy = "never"

    provider = _BaselineProvider()
    runtime = assemble_runtime(
        config,
        pico_config,
        provider=provider,
        cron_service=None,
        interactive=False,
    )
    request = TurnRequest(
        origin=Origin.USER,
        source=Source(
            channel="cli",
            chat_id="phase1",
            sender_id="user",
            chat_type=ChatType.DM,
        ),
        text="Read phase1-sentinel.txt and return the deterministic marker.",
        conversation="cli:phase1-baseline",
    )
    rendered: list[str] = []
    errors: list[str] = []
    teardown = None

    try:
        await runtime.start_memory_backend()
        scheduler, hub, teardown = build_repl(
            runtime.agent_loop,
            "cli",
            rendered.append,
            render_error=errors.append,
        )
        runtime.agent_loop.subagents.set_submit(scheduler.submit)
        outcome = await scheduler.submit(request).result()
        await hub.wait_idle("cli")
    finally:
        if teardown is not None:
            await teardown()
        await runtime.close()

    assert outcome is not None
    assert outcome.explicit_reply is True
    assert outcome.tool_calls == 1
    assert outcome.tool_failures == 0
    assert rendered == ["PHASE1_RUNTIME_OK"]
    assert errors == []
    assert provider.calls == 2
    assert "read_file" in provider.tool_schemas[0]

    session = runtime.session_manager.get_or_create(request.conversation)
    assert any(message.get("content") == request.text for message in session.messages)
    assert any("PHASE1_SENTINEL" in str(message.get("content")) for message in session.messages)

    records = runtime.agent_loop.effect_journal.load()
    read_records = [record for record in records if record.tool_name == "read_file"]
    assert read_records
    assert read_records[-1].status.value == "committed"
    assert runtime.agent_loop._closed is True
