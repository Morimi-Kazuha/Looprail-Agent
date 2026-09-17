"""Budgeted Context Engine and long-turn contract tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from looprail.agent.loop import AgentLoop
from looprail.agent.recovery import RecoveryState
from looprail.config.looprail import ContextConfig
from looprail.context_engine import (
    ContextAssembler,
    ContextBudgetError,
    HistoryTrimmer,
    RuntimeContextTrimmer,
    TurnContext,
    resolve_context_limits,
)
from looprail.memory_engine.base import TokenBudget
from looprail.providers.base import GenerationSettings, LLMProvider, LLMResponse
from looprail.spine.message import ChatType, Source
from looprail.spine.turn import Origin, TurnRequest


class _LimitProvider:
    def __init__(self, context_window_tokens: int) -> None:
        self.context_window_tokens = context_window_tokens


class _DeterministicCounter:
    """A stable test counter that makes pressure and retention decisions reproducible."""

    @staticmethod
    def _size(value: object) -> int:
        if isinstance(value, str):
            return max(1, len(value) // 4)
        if value is None:
            return 1
        return max(1, len(json.dumps(value, ensure_ascii=False)) // 4)

    def estimate_prompt_tokens(self, messages, tools, model):
        total = 0
        for message in messages:
            total += self._size(message.get("content"))
            total += self._size(message.get("tool_calls")) if message.get("tool_calls") else 0
            total += 1
        if tools:
            total += self._size(tools)
        return total, "deterministic_test_counter"


class _AnswerProvider(LLMProvider):
    def __init__(self, *, context_window_tokens: int = 16_384) -> None:
        super().__init__(api_key="test")
        self.context_window_tokens = context_window_tokens
        self.generation = GenerationSettings(max_tokens=256)
        self.curator_calls = 0
        self.main_messages: list[list[dict]] = []

    async def chat(
        self,
        messages,
        tools=None,
        model=None,
        max_tokens=4096,
        temperature=0.7,
        reasoning_effort=None,
        tool_choice=None,
    ):
        tool_names = {tool.get("function", {}).get("name") for tool in (tools or [])}
        if "curator_build_context" in tool_names:
            self.curator_calls += 1
            return LLMResponse(content="no context plan", finish_reason="stop")
        self.main_messages.append([dict(message) for message in messages])
        return LLMResponse(content="long task complete", finish_reason="stop")

    def get_default_model(self) -> str:
        return "context-model"


def _tool_call(call_id: str, name: str = "read_file") -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": "{}"},
    }


def _tool_exchange(call_id: str, content: str, *, name: str = "read_file") -> list[dict]:
    return [
        {"role": "assistant", "content": "", "tool_calls": [_tool_call(call_id, name)]},
        {"role": "tool", "tool_call_id": call_id, "name": name, "content": content},
    ]


def test_budget_uses_smaller_provider_limit_and_reserves_output_and_margin() -> None:
    limits = resolve_context_limits(
        _LimitProvider(4_096),
        "context-model",
        configured_context_limit=8_192,
        reserved_output=512,
        runtime_margin=128,
    )

    assert limits.effective_context_limit == 4_096
    assert limits.input_context_budget == 3_456
    assert limits.source == "provider_and_configured"

    budget = TokenBudget(
        context_length=limits.effective_context_limit,
        reserved_output=limits.reserved_output,
        reserved_tools=100,
        reserved_system=200,
        available_history=3_156,
        runtime_margin=limits.runtime_margin,
        provider_context_limit=limits.provider_context_limit,
        budget_source=limits.source,
    )
    assert budget.total_reserved == 940
    assert budget.input_context_budget == 3_456


def test_unknown_provider_limit_is_explicit_configured_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("looprail.context_engine.budget.resolve_context_window", lambda *args, **kwargs: None)

    limits = resolve_context_limits(
        object(),
        "provider-without-limit-metadata",
        configured_context_limit=4_096,
        reserved_output=256,
        runtime_margin=128,
    )

    assert limits.provider_context_limit is None
    assert limits.effective_context_limit == 4_096
    assert limits.input_context_budget == 3_712
    assert limits.source == "configured_fallback"


def test_short_history_stays_below_budget_and_reports_keep_decision() -> None:
    provider = _DeterministicCounter()
    trimmer = HistoryTrimmer(provider, "context-model", lambda: [], 2_000, runtime_margin_tokens=100)
    messages = [{"role": "user", "content": "short task"}, {"role": "assistant", "content": "done"}]

    built, outcome = trimmer.trim(
        session_messages=messages,
        ids=[0, 1],
        protected_ids={0, 1},
        reserved_output=200,
        runtime_margin_tokens=100,
        build_messages=lambda history: [
            {"role": "system", "content": "system"},
            *history,
            {"role": "user", "content": "current"},
        ],
    )

    assert outcome.ok is True
    assert outcome.included_ids == [0, 1]
    assert any(item["decision"] == "KEEP" for item in outcome.decisions)
    assert HistoryTrimmer.structural_errors(built) == []


def test_history_budget_drops_a_whole_low_priority_turn_and_keeps_tool_pair() -> None:
    provider = _DeterministicCounter()
    messages = [
        {"role": "user", "content": "old setup"},
        *_tool_exchange("old-call", "old output " * 300),
        {"role": "assistant", "content": "old conclusion"},
        {"role": "user", "content": "recent request"},
        {"role": "assistant", "content": "recent answer"},
    ]
    trimmer = HistoryTrimmer(provider, "context-model", lambda: [], 550)

    built, outcome = trimmer.trim(
        session_messages=messages,
        ids=list(range(len(messages))),
        protected_ids={4, 5},
        reserved_output=50,
        runtime_margin_tokens=50,
        priority_scores={mid: (0.9 if mid >= 4 else 0.1) for mid in range(len(messages))},
        build_messages=lambda history: [
            {"role": "system", "content": "system"},
            *history,
            {"role": "user", "content": "current"},
        ],
    )

    assert outcome.ok is True
    assert outcome.included_ids == [4, 5]
    assert any(item["decision"] == "DROP" for item in outcome.decisions)
    assert HistoryTrimmer.structural_errors(built) == []


def test_runtime_compaction_deduplicates_success_without_breaking_call_result_boundary() -> None:
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "inspect the same file twice"},
        *_tool_exchange("call-a", "same successful observation " * 160),
        *_tool_exchange("call-b", "same successful observation " * 160),
        {"role": "user", "content": "current request"},
    ]
    observation_meta = {
        "call-a": {"name": "read_file", "fingerprint": "same-digest", "failed": False},
        "call-b": {"name": "read_file", "fingerprint": "same-digest", "failed": False},
    }
    trimmer = RuntimeContextTrimmer(
        _DeterministicCounter(),
        "context-model",
        lambda: [],
        context_limit=900,
        reserved_output=100,
        runtime_margin=100,
    )

    outcome = trimmer.fit(
        messages,
        turn_start_idx=len(messages) - 1,
        observation_meta=observation_meta,
        protected_positions={len(messages) - 1},
    )
    by_call_id = {
        message.get("tool_call_id"): message
        for message in outcome.messages
        if message.get("role") == "tool"
    }

    assert outcome.changed is True
    assert "call-a" in outcome.compacted_tool_call_ids
    assert "duplicate successful output" in by_call_id["call-a"]["content"]
    assert "duplicate successful output" not in by_call_id["call-b"]["content"]
    assert outcome.estimated_tokens <= outcome.input_context_budget
    assert HistoryTrimmer.structural_errors(outcome.messages) == []


def test_runtime_compaction_retains_latest_failure_and_current_task() -> None:
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "establish repository facts"},
        *_tool_exchange("call-old-a", "successful output " * 240),
        {"role": "user", "content": "repeat the inspection"},
        *_tool_exchange("call-old-b", "successful output " * 240),
        {"role": "user", "content": "the latest attempt failed"},
        *_tool_exchange("call-failure", "ERROR: failure evidence must remain visible " * 240),
        {"role": "user", "content": "current task: continue from the known failure"},
    ]
    observation_meta = {
        "call-old-a": {"name": "read_file", "fingerprint": "old-a", "failed": False},
        "call-old-b": {"name": "read_file", "fingerprint": "old-b", "failed": False},
        "call-failure": {"name": "read_file", "fingerprint": "failure", "failed": True},
    }
    trimmer = RuntimeContextTrimmer(
        _DeterministicCounter(),
        "context-model",
        lambda: [],
        context_limit=1_800,
        reserved_output=100,
        runtime_margin=100,
    )

    outcome = trimmer.fit(
        messages,
        turn_start_idx=len(messages) - 1,
        observation_meta=observation_meta,
        protected_positions={len(messages) - 1},
    )
    tool_messages = {
        message.get("tool_call_id"): message
        for message in outcome.messages
        if message.get("role") == "tool"
    }

    assert outcome.changed is True
    assert outcome.estimated_tokens <= outcome.input_context_budget
    assert "call-failure" in tool_messages
    assert "failure evidence" in tool_messages["call-failure"]["content"]
    assert any(
        message.get("role") == "user" and "current task" in message.get("content", "")
        for message in outcome.messages
    )
    assert HistoryTrimmer.structural_errors(outcome.messages) == []


@pytest.mark.asyncio
async def test_context_assembler_exposes_budget_estimate_and_protected_decisions() -> None:
    assembler = ContextAssembler([], lambda: [])
    budget = TokenBudget(
        context_length=4_096,
        reserved_output=256,
        reserved_tools=0,
        reserved_system=0,
        available_history=3_500,
        runtime_margin=128,
    )

    assembled = await assembler.assemble(
        "context:short",
        [],
        budget,
        turn=TurnContext(current_message="keep the current task explicit", channel="test", chat_id="short"),
    )

    assert assembled.metadata["context_estimate"]["after"] <= budget.input_context_budget
    decisions = assembled.metadata["context_decisions"]
    current = next(item for item in decisions if item["source"] == "current_user_request")
    assert current["decision"] == "KEEP"
    assert current["protected"] is True
    assert assembled.metadata["context_structural_errors"] == []


@pytest.mark.asyncio
async def test_protected_fixed_context_overflow_is_explicit() -> None:
    assembler = ContextAssembler([], lambda: [])
    budget = TokenBudget(
        context_length=128,
        reserved_output=32,
        reserved_tools=0,
        reserved_system=0,
        available_history=64,
        runtime_margin=32,
    )

    with pytest.raises(ContextBudgetError) as caught:
        await assembler.assemble(
            "context:overflow",
            [],
            budget,
            turn=TurnContext(current_message="protected current request " * 80),
        )

    assert caught.value.reason == "protected_fixed_context_exceeds_budget"
    assert caught.value.input_context_budget == 64


def test_runtime_protected_context_overflow_is_normalized() -> None:
    trimmer = RuntimeContextTrimmer(
        _DeterministicCounter(),
        "context-model",
        lambda: [],
        context_limit=128,
        reserved_output=32,
        runtime_margin=32,
    )
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "protected current request " * 80},
    ]

    with pytest.raises(ContextBudgetError) as caught:
        trimmer.fit(messages, turn_start_idx=1, protected_positions={1})

    assert caught.value.reason == "protected_runtime_context_exceeds_budget"
    assert caught.value.to_dict()["error"] == "context_budget_exceeded"


@pytest.mark.asyncio
async def test_unknown_recovery_evidence_is_budgeted_and_not_persisted(tmp_path: Path) -> None:
    provider = _AnswerProvider()
    agent = AgentLoop(
        provider=provider,
        workspace=tmp_path,
        model="context-model",
        context_window_tokens=8_192,
        context_config=ContextConfig(runtime_margin_tokens=256),
    )
    session_key = "cli:context-recovery"
    session = agent.sessions.get_or_create(session_key)
    agent._resume_projections[session_key] = RecoveryState(
        session_key=session_key,
        last_turn_id="turn-old",
        last_turn_status="interrupted",
        checkpoint_id="checkpoint-old",
        checkpoint_files=("src/app.py",),
        unknown_effect_ids=("effect-unknown",),
        projection_id="projection-context",
    )
    metadata: dict = {}
    budget_holder: dict = {}

    messages = await agent._assemble_context_messages(
        session=session,
        session_key=session_key,
        current_message="continue the interrupted task",
        channel="cli",
        chat_id="context-recovery",
        metadata_sink=metadata,
        budget_sink=budget_holder,
    )

    assert "effect-unknown" in messages[-1]["content"]
    assert metadata["recovery_unknown_effect_ids"] == ["effect-unknown"]
    assert any(item["source"] == "recovery_evidence" and item["protected"] for item in metadata["context_decisions"])
    assert metadata["context_estimate"]["after"] <= metadata["context_budget"]["input_context_budget"]

    persisted = agent._save_turn(
        session,
        messages,
        len(messages) - 1,
        context_only_prefixes=metadata["_context_only_prefixes"],
    )
    assert persisted[0]["content"] == "continue the interrupted task"
    assert "Recovery" not in persisted[0]["content"]


@pytest.mark.asyncio
async def test_real_agentloop_mainline_handles_long_deterministic_task(tmp_path: Path) -> None:
    provider = _AnswerProvider(context_window_tokens=16_384)
    loop = AgentLoop(
        provider=provider,
        workspace=tmp_path,
        model="context-model",
        context_window_tokens=8_192,
        context_config=ContextConfig(
            engine="curator",
            fast_path_threshold=0.0,
            runtime_margin_tokens=512,
        ),
    )
    session_key = "cli:context-long"
    session = loop.sessions.get_or_create(session_key)
    for index in range(10):
        session.add_message(
            "user",
            f"historical task turn {index}: preserve the repository contract and inspect evidence",
        )
        session.add_message(
            "assistant",
            f"historical answer {index}: the next step is deterministic verification",
        )
        if index < 7:
            session.messages.extend(
                _tool_exchange(
                    f"historical-call-{index}",
                    f"raw tool observation {index}: " + ("repository output " * 180),
                )
            )
    session.add_message("user", "latest failure: command returned ERROR and must be checked before retry")
    session.messages.extend(
        _tool_exchange(
            "latest-failure-call",
            "ERROR: latest failure evidence " + ("do not erase this diagnostic " * 120),
        )
    )
    loop.sessions.save(session)

    metadata: dict = {}
    result = await loop._process_message(
        TurnRequest(
            origin=Origin.USER,
            source=Source(
                channel="cli",
                chat_id="context-long",
                sender_id="user",
                chat_type=ChatType.DM,
            ),
            text="continue the long coding task using the latest failure evidence",
        ),
        session_key=session_key,
        context_metadata_sink=metadata,
    )

    assert result == ("long task complete", [])
    assert provider.curator_calls >= 1
    assert len(provider.main_messages) == 1
    assert metadata["engine"] == "context_assembler"
    assert metadata["context_estimate"]["after"] <= metadata["context_budget"]["input_context_budget"]
    assert metadata["context_structural_errors"] == []
    assert any(item["decision"] in {"DROP", "COMPACT"} for item in metadata["context_decisions"])
    assert HistoryTrimmer.structural_errors(provider.main_messages[0]) == []
    assert any(
        message.get("role") == "user" and "latest failure evidence" in message.get("content", "")
        for message in provider.main_messages[0]
    )
