"""Exercise production assembly, Spine, tool effects, sessions and decision trace."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from looprail.agent.loop import AgentLoop
from looprail.agent.loop.adaptive_recovery import RecoveryPolicy
from looprail.config.looprail import JevConfig
from looprail.providers.jev_decision import QUESTION_ID, build_recovery_policy
from looprail.tracing import spans
from scripts.jev_recovery_demo import FakeDecisionAdvisor, ScriptedRecoveryProvider, run_demo


@pytest.fixture
def decision_trace(tmp_path, monkeypatch):
    directory = tmp_path / "traces"
    monkeypatch.setenv("LOOPRAIL_TRACING", "1")
    monkeypatch.setenv("LOOPRAIL_TRACING_DIR", str(directory))
    spans._store = None
    yield directory
    spans._store = None


def decisions(directory):
    file = directory / "logs" / "audit-spans.log"
    if not file.exists():
        return []
    return [
        row
        for line in file.read_text(encoding="utf-8").splitlines()
        if (row := json.loads(line))["name"] == "agent.recovery.decision"
    ]


@pytest.mark.parametrize(
    "action,expected_tools",
    [
        ("RECHECK_INPUTS", ["read_file", "read_file", "list_dir", "read_file"]),
        ("USE_ALTERNATIVE", ["read_file", "read_file", "read_file"]),
        ("STOP", ["read_file", "read_file"]),
    ],
)
@pytest.mark.asyncio
async def test_decision_policy_changes_actual_execution(tmp_path, decision_trace, action, expected_tools):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "answers": {
                    QUESTION_ID: {
                        "type": "choice",
                        "choice": action,
                        "confidence": 0.95,
                        "probabilities": {
                            a: 0.98 if a == action else 0.01 for a in ("RECHECK_INPUTS", "USE_ALTERNATIVE", "STOP")
                        },
                    }
                }
            },
        )

    policy = build_recovery_policy(
        JevConfig(enabled=True), {"LOOPRAIL_OPENROUTER_API_KEY": "test-key"}, transport=httpx.MockTransport(handler)
    )
    result = await run_demo(tmp_path / "workspace", policy)
    assert len(requests) == 1
    assert requests[0]["state"]["consecutive_failures"] == 2
    assert "arguments" not in requests[0]["state"] and "result" not in requests[0]["state"]
    assert result["tools_executed"] == expected_tools
    assert result["tool_failures"] == 2 and result["session_persisted"]
    expected_status = "interrupted" if action == "STOP" else "completed"
    assert result["persisted_turn_status"] == expected_status
    assert result["persisted_summary"] == result["result"][-1]
    assert len(result["errors"]) == 2 and all(error.startswith("Tool failed:") for error in result["errors"])
    assert result["result"] == (
        ["BLOCKED: input unavailable; task remains incomplete."]
        if action == "STOP"
        else ["RECOVERED: read the verified local input."]
    )
    rows = decisions(decision_trace)
    assert len(rows) == 1 and rows[0]["attributes"]["turn.id"] == "jev-demo-turn"
    attrs = rows[0]["attributes"]
    assert attrs["decision_source"] == "jev" and attrs["selected_action"] == action
    assert not attrs["fallback_used"] and attrs["decision_type"] == "TOOL_RECOVERY"
    assert "test-key" not in json.dumps(rows)
    assert any(event["name"] == "JEV_DECISION" for event in rows[0]["events"])


@pytest.mark.asyncio
async def test_normal_turn_never_calls_jev(tmp_path, decision_trace):
    advisor = FakeDecisionAdvisor()
    result = await run_demo(tmp_path / "normal", RecoveryPolicy(advisor), case="normal")
    assert advisor.calls == 0 and decisions(decision_trace) == []
    assert result["tool_calls"] == 1 and result["tool_failures"] == 0


@pytest.mark.asyncio
async def test_failure_preserves_original_baseline_execution(tmp_path, decision_trace):
    baseline = await run_demo(tmp_path / "baseline", None)
    unavailable = await run_demo(tmp_path / "fallback", RecoveryPolicy(None))
    assert baseline == unavailable
    rows = decisions(decision_trace)
    assert len(rows) == 1 and rows[0]["attributes"]["reason"] == "missing_key"
    assert rows[0]["attributes"]["selected_action"] == "BASELINE"


@pytest.mark.asyncio
async def test_http_failure_cannot_break_loop(tmp_path, decision_trace):
    policy = build_recovery_policy(
        JevConfig(enabled=True),
        {"LOOPRAIL_OPENROUTER_API_KEY": "test-key"},
        transport=httpx.MockTransport(lambda _: httpx.Response(503)),
    )
    result = await run_demo(tmp_path / "http-fallback", policy)
    assert result["tool_calls"] == 6 and all(error.startswith("Tool failed:") for error in result["errors"])
    assert decisions(decision_trace)[0]["attributes"]["reason"] == "http_error"


@pytest.mark.parametrize("max_iterations,expected_calls", [(2, 0), (6, 1)])
@pytest.mark.asyncio
async def test_advisor_budget_and_stop_status(tmp_path, max_iterations, expected_calls):
    advisor = FakeDecisionAdvisor("STOP")
    provider = ScriptedRecoveryProvider()
    agent = AgentLoop(
        provider,
        tmp_path,
        max_iterations=max_iterations,
        restrict_to_workspace=True,
        interactive=False,
        recovery_policy=RecoveryPolicy(advisor),
    )
    try:
        text, tools, messages, outcome = await agent._run_agent_loop(
            [{"role": "user", "content": "read the local input"}],
            session_key="s",
            turn_id="turn",
        )
    finally:
        await agent.close()
    assert advisor.calls == expected_calls
    assert outcome.status == "interrupted"  # a summary never converts blocked work to completion
    assert tools == ["read_file", "read_file"]
    assert "incomplete" in text
    assert messages[-1]["role"] == "assistant"


@pytest.mark.asyncio
async def test_only_once_per_turn_even_when_model_ignores_advice(tmp_path):
    from tests.test_agent_loop_tool_loop_break import _AlwaysFailsSameToolProvider

    advisor = FakeDecisionAdvisor()
    agent = AgentLoop(
        _AlwaysFailsSameToolProvider(),
        tmp_path,
        max_iterations=6,
        interactive=False,
        recovery_policy=RecoveryPolicy(advisor),
    )
    try:
        for key in ("session-a", "session-b"):
            await agent._run_agent_loop([{"role": "user", "content": "go"}], session_key=key)
    finally:
        await agent.close()
    assert advisor.calls == 2


@pytest.mark.asyncio
async def test_cancelled_advisor_propagates_without_more_tools(tmp_path):
    class CancelAdvisor:
        async def advise(self, context):
            raise asyncio.CancelledError()

    provider = ScriptedRecoveryProvider()
    agent = AgentLoop(
        provider, tmp_path, max_iterations=6, interactive=False, recovery_policy=RecoveryPolicy(CancelAdvisor())
    )
    try:
        with pytest.raises(asyncio.CancelledError):
            await agent._run_agent_loop([{"role": "user", "content": "go"}])
    finally:
        await agent.close()
    assert provider.tool_calls == ["read_file", "read_file"]


@pytest.mark.asyncio
async def test_stop_streams_tool_free_summary_and_safe_static_fallback(tmp_path, monkeypatch):
    provider = ScriptedRecoveryProvider()
    agent = AgentLoop(
        provider,
        tmp_path,
        max_iterations=6,
        interactive=False,
        recovery_policy=RecoveryPolicy(FakeDecisionAdvisor("STOP")),
    )
    deltas, tool_flags = [], []

    async def fake_stream(**kwargs):
        tool_flags.append(kwargs.get("tools"))
        if kwargs.get("tools") is None:
            raise RuntimeError("summary provider unavailable")
        return await provider.chat_with_retry(**kwargs)

    async def emit_delta(text):
        deltas.append(text)

    monkeypatch.setattr(agent, "_llm_call_stream", fake_stream)
    try:
        text, tools, messages, outcome = await agent._run_agent_loop(
            [{"role": "user", "content": "read input"}],
            on_token_delta=emit_delta,
        )
    finally:
        await agent.close()
    assert outcome.status == "interrupted" and len(tools) == 2
    assert tool_flags[-1] is None
    assert deltas == [text] and "incomplete" in text and "maximum" not in text
    assert messages[-1]["content"] == text


def test_shared_assembly_wires_feature_flag(tmp_path, monkeypatch):
    from looprail.cli._runtime_assembly import assemble_runtime
    from looprail.config.looprail import LooprailConfig
    from looprail.config.schema import Config

    config = Config()
    config.agents.defaults.workspace = str(tmp_path)
    features = LooprailConfig(base=config)
    features.memory.backend = None
    features.runtime.checkpoint.policy = "never"
    monkeypatch.setenv("LOOPRAIL_JEV_ENABLED", "true")
    monkeypatch.delenv("LOOPRAIL_OPENROUTER_API_KEY", raising=False)
    runtime = assemble_runtime(
        config, features, provider=ScriptedRecoveryProvider(), cron_service=None, interactive=False
    )
    try:
        assert runtime.agent_loop.recovery_policy is not None
        assert runtime.agent_loop.recovery_policy.unavailable_reason == "missing_key"
    finally:
        asyncio.run(runtime.close())


@pytest.mark.asyncio
async def test_stop_ignores_summary_tool_calls_and_persists_interruption(tmp_path):
    from looprail.providers.base import LLMResponse, ToolCallRequest

    class SideEffectProvider(ScriptedRecoveryProvider):
        def __init__(self):
            super().__init__()
            self.summary_tools = []

        async def chat_with_retry(self, **kwargs):
            if kwargs.get("tools") is None:
                self.summary_tools.append(kwargs.get("tools"))
                return LLMResponse(
                    content="BLOCKED: task remains incomplete.",
                    tool_calls=[
                        ToolCallRequest(
                            "unsafe-write", "write_file", {"path": "side-effect.txt", "content": "should never run"}
                        )
                    ],
                    finish_reason="tool_calls",
                )
            if self.calls >= 2:
                return LLMResponse(
                    tool_calls=[
                        ToolCallRequest(
                            "later-write", "write_file", {"path": "side-effect.txt", "content": "should never run"}
                        )
                    ]
                )
            return await super().chat_with_retry(**kwargs)

    advisor = FakeDecisionAdvisor("STOP")
    provider = SideEffectProvider()
    workspace = tmp_path / "stop"
    result = await run_demo(workspace, RecoveryPolicy(advisor), provider=provider)
    assert advisor.calls == 1 and provider.summary_tools == [None]
    assert result["tools_executed"] == ["read_file", "read_file"]
    assert result["tool_calls"] == 2 and not (workspace / "side-effect.txt").exists()
    assert result["persisted_turn_status"] == "interrupted"
    assert result["session_persisted"] and "incomplete" in result["persisted_summary"]
