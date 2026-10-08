"""Deterministic five-stage contracts and real AgentLoop continuation."""

from __future__ import annotations

import asyncio
import copy
import json
import re

import pytest

from looprail.agent.loop import AgentLoop
from looprail.agent.tools.base import Tool, ToolResult
from looprail.config.looprail import ContextConfig
from looprail.context_engine import ContextAssembler, ContextBudgetError, HistoryTrimmer, TurnContext
from looprail.context_engine.compression import SUMMARY_FIELDS, CuratorCompression
from looprail.context_engine.curator import CuratorArchiveStore
from looprail.context_engine.segments.curator import CuratorSegmentBuilder
from looprail.memory_engine.base import TokenBudget
from looprail.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from looprail.spine import ChatType, Origin, Source, TurnRequest


class Provider(LLMProvider):
    def __init__(self, mode="valid"):
        super().__init__(api_key="fake")
        self.mode = mode
        self.summary_calls = 0
        self.main_calls = []

    def estimate_prompt_tokens(self, messages, tools, model):
        return sum(4 + len(json.dumps(msg, ensure_ascii=False)) // 4 for msg in messages) + len(
            json.dumps(tools or [])
        ) // 4, "r2_deterministic"

    def get_default_model(self):
        return "fake-main"

    async def chat(self, messages, tools=None, **kwargs):
        if "Looprail Auto Compaction" in messages[0].get("content", ""):
            self.summary_calls += 1
            if self.mode == "timeout":
                await asyncio.Event().wait()
            if self.mode == "error":
                return LLMResponse(content="bad", finish_reason="error")
            if self.mode == "invalid":
                return LLMResponse(content="not JSON")
            sources = json.loads(messages[1]["content"])
            summary = {field: [] for field in SUMMARY_FIELDS}
            patterns = {
                "current_goal": "Implement",
                "user_constraints": "Do not",
                "test_results": "ERROR:",
                "changed_files": "Successfully wrote",
                "current_blockers": "ERROR:",
                "next_steps": "Next:",
                "important_decisions": "Decision:",
            }
            for field, pattern in patterns.items():
                for mid, text in sources.items():
                    line = next((line for line in text.splitlines() if line.startswith(pattern)), None)
                    if line:
                        summary[field] = [{"message_id": int(mid), "quote": line}]
                        break
            if not any(summary.values()):
                mid, text = next(iter(sources.items()))
                summary["current_goal"] = [{"message_id": int(mid), "quote": text.splitlines()[0]}]
            if self.mode == "hallucination":
                summary["test_results"] = [{"message_id": 0, "quote": "All tests passed; task complete."}]
            if self.mode == "oversized":
                entry = next(entries[0] for entries in summary.values() if entries)
                summary = {field: [entry] * 12 for field in SUMMARY_FIELDS}
            return LLMResponse(content=json.dumps(summary))
        self.main_calls.append(copy.deepcopy(messages))
        return LLMResponse(content="Continue fixing app.py; tests remain failed; do not add dependencies.")


def call(cid, name="read_file", args=None):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args or {})}}


def exchange(cid, text, name="read_file", args=None):
    return [
        {"role": "assistant", "content": "", "tool_calls": [call(cid, name, args)]},
        {"role": "tool", "name": name, "tool_call_id": cid, "content": text},
    ]


def budget(limit):
    return TokenBudget(limit + 100, 100, 0, 0, limit)


def curator(tmp_path, provider, **config):
    return CuratorSegmentBuilder(
        tmp_path, ContextConfig(protect_first_n=0, **config), provider, "fake-main", 100_000, lambda: []
    )


async def assemble(tmp_path, history, limit, *, provider=None, relevance=None, **config):
    provider = provider or Provider()
    builder = curator(tmp_path, provider, **config)
    if relevance is not None:
        manifest = builder.archive.build_manifest("r2", history)
        for item in manifest:
            item.relevance = relevance.get(item.id, 0.5)
        builder.archive.save_manifest("r2", manifest)
    engine = ContextAssembler([builder], lambda: [], provider=provider, model="fake-main")
    result = await engine.assemble(
        "r2", history, budget(limit), turn=TurnContext(current_message="Continue the coding task.")
    )
    return result, provider, builder


def stages(result):
    return [event["stage"] for event in result.metadata["compression_stages"]]


@pytest.mark.asyncio
async def test_a_fast_path_never_calls_summary_even_above_old_threshold(tmp_path):
    result, provider, _ = await assemble(
        tmp_path,
        [{"role": "user", "content": "short"}, {"role": "assistant", "content": "ack"}],
        2000,
        fast_path_threshold=0.0,
    )
    assert result.metadata["path"] == "fast"
    assert stages(result) == []
    assert provider.summary_calls == 0


@pytest.mark.asyncio
async def test_b_single_result_offloaded_recoverable_and_pair_intact(tmp_path):
    raw = "HEAD\n" + "output " * 5000 + "\nTAIL"
    history = [{"role": "user", "content": "read source"}, *exchange("huge", raw)]
    saved = copy.deepcopy(history)
    result, provider, builder = await assemble(tmp_path, history, 1300)
    assert stages(result) == ["tool_result_offloading"]
    assert provider.summary_calls == 0
    assert HistoryTrimmer.structural_errors(result.messages) == []
    tool = next(msg for msg in result.messages if msg.get("role") == "tool")
    assert tool["tool_call_id"] == "huge" and "Archive:" in tool["content"]
    ref = result.metadata["compression_stages"][0]["archive_refs"][0]
    archived = (tmp_path / ref.split("#")[0]).read_text(encoding="utf-8")
    assert json.loads(archived.splitlines()[1])["message"]["content"] == raw
    assert builder.archive.retrieve([ref], max_tokens=400)["results"][0]["messages"]
    assert history == saved


@pytest.mark.asyncio
async def test_stage1_per_result_cap_applies_even_when_global_budget_has_room(tmp_path):
    history = [{"role": "user", "content": "source"}, *exchange("huge", "output " * 5000)]
    result, provider, _ = await assemble(tmp_path, history, 100_000)
    assert stages(result) == ["tool_result_offloading"]
    assert result.metadata["compression_stages"][0]["reason"] == "single-result size budget exceeded"
    assert provider.summary_calls == 0


@pytest.mark.asyncio
async def test_long_failed_result_retains_bounded_failure_evidence_and_archive(tmp_path):
    history = [{"role": "user", "content": "debug"}, *exchange("failure", "ERROR: " + "long diagnostic " * 3000)]
    result, provider, _ = await assemble(tmp_path, history, 2400)
    output = next(msg["content"] for msg in result.messages if msg.get("tool_call_id") == "failure")
    assert "failure preserved" in output and "Failure evidence" in output and "Archive:" in output
    assert stages(result) == ["tool_result_offloading"]
    assert provider.summary_calls == 0


@pytest.mark.asyncio
async def test_live_projection_preserves_synthetic_recovery_flag(tmp_path):
    from looprail.context_engine.history_trimmer import RuntimeContextTrimmer

    provider = Provider()
    builder = curator(tmp_path, provider)
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "original task"},
        *exchange("long", "output " * 5000),
        {"role": "assistant", "content": "(empty)", "_recovery_synthetic": True},
        {"role": "user", "content": "Harness nudge", "_recovery_synthetic": True},
    ]
    trimmer = RuntimeContextTrimmer(
        provider, "fake-main", lambda: [], context_limit=2100, reserved_output=100, runtime_margin=0
    )
    fitted = await builder.fit_runtime(trimmer, messages, session_key="s", turn_start_idx=1)
    assert fitted.messages[-1]["_recovery_synthetic"] is True
    assert fitted.messages[-2]["_recovery_synthetic"] is True
    assert fitted.messages[1]["content"] == "original task"


@pytest.mark.asyncio
async def test_c_snip_low_relevance_complete_turn_preserves_user_constraints(tmp_path):
    history = []
    for i in range(10):
        history.extend(
            [
                {"role": "user", "content": "Do not change config." if i == 0 else f"old task {i}"},
                {"role": "assistant", "content": "redundant explanation " * 150 if i == 3 else "ack"},
            ]
        )
    result, provider, _ = await assemble(tmp_path, history, 1100, relevance={6: 0.1, 7: 0.1})
    assert stages(result) == ["history_snip"]
    assert result.metadata["compression_stages"][0]["message_ids"] == [6, 7]
    assert "Do not change config." in json.dumps(result.messages)
    assert "old task 3" in result.messages[0]["content"]
    assert provider.summary_calls == 0


@pytest.mark.asyncio
async def test_d_old_observations_microcompact_keep_recent_and_errors(tmp_path):
    history = []
    for i in range(5):
        history.extend(
            [
                {"role": "user", "content": f"inspect {i}"},
                *exchange(f"c{i}", "same output " * 350 if i < 3 else "recent evidence"),
                {"role": "assistant", "content": "ack"},
            ]
        )
    result, provider, _ = await assemble(tmp_path, history, 1800)
    assert "microcompaction" in stages(result)
    assert "tool_result_offloading" not in stages(result)
    assert "context_collapse" not in stages(result)
    assert provider.summary_calls == 0
    assert HistoryTrimmer.structural_errors(result.messages) == []
    assert next(msg for msg in result.messages if msg.get("tool_call_id") == "c4")["content"] == "recent evidence"


@pytest.mark.asyncio
async def test_e_closed_turn_collapse_preserves_decision_and_archive(tmp_path):
    history = [
        {"role": "user", "content": "Do not add dependencies."},
        {"role": "assistant", "content": "debug transcript " * 500},
        {"role": "assistant", "content": "Decision: reuse the existing parser."},
        {"role": "user", "content": "next subtask"},
        {"role": "assistant", "content": "ack"},
        {"role": "user", "content": "latest subtask"},
        {"role": "assistant", "content": "ack"},
    ]
    result, provider, builder = await assemble(tmp_path, history, 900)
    assert stages(result) == ["context_collapse"]
    assert "Decision: reuse the existing parser." in result.messages[0]["content"]
    assert "Do not add dependencies." in result.messages[0]["content"]
    assert provider.summary_calls == 0
    assert result.metadata["compression_stages"][0]["archive_refs"]
    assert not (tmp_path / "memory/MEMORY.md").exists()


def coding_history():
    return [
        {"role": "user", "content": "Implement retry handling.\nDo not add dependencies or change the public API."},
        *exchange("read", "parser source " * 250),
        *exchange("edit", "Successfully wrote app.py", "write_file", {"path": "app.py", "content": "fix"}),
        *exchange("test", "ERROR: pytest failed: retry count is 3, expected 2.\nExit code: 1", "exec"),
        {
            "role": "assistant",
            "content": "irrelevant debug detail " * 300
            + "\nDecision: keep the public API.\nNext: fix retry count in app.py and rerun pytest.",
        },
    ]


async def run_coding_scenario(workspace):
    """Actually execute fake read/write/test tools, then continue a second Turn."""
    executions = []

    class CodingTool(Tool):
        description = "deterministic coding fixture"
        parameters = {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}}

        def __init__(self, name, result):
            self._name, self.result = name, result

        @property
        def name(self):
            return self._name

        async def execute(self, **kwargs):
            executions.append((self.name, kwargs))
            return self.result

    class CodingProvider(Provider):
        step = 0

        async def chat(self, messages, tools=None, **kwargs):
            if "Looprail Auto Compaction" in messages[0].get("content", ""):
                return await super().chat(messages, tools=tools, **kwargs)
            self.main_calls.append(copy.deepcopy(messages))
            if self.step < 3:
                specs = [
                    ("read_file", {"path": "app.py"}),
                    ("write_file", {"path": "app.py", "content": "retry fix"}),
                    ("exec", {}),
                ]
                name, args = specs[self.step]
                self.step += 1
                return LLMResponse(
                    content="", tool_calls=[ToolCallRequest(id=f"coding-{self.step}", name=name, arguments=args)]
                )
            if self.step == 3:
                self.step += 1
                return LLMResponse(
                    content="diagnostic details " * 700
                    + "\nDecision: preserve the public API.\nNext: fix retry count in app.py and rerun pytest."
                )
            return LLMResponse(content="Continue fixing app.py; tests remain failed; do not add dependencies.")

    provider = CodingProvider()
    loop = AgentLoop(
        provider=provider,
        workspace=workspace,
        model="fake-main",
        context_window_tokens=100_000,
        context_config=ContextConfig(protect_first_n=0, runtime_margin_tokens=0),
    )
    for name, result in [
        ("read_file", "parser source " * 400),
        ("write_file", "Successfully wrote app.py"),
        ("exec", ToolResult("ERROR: pytest failed: retry count is 3, expected 2.\nExit code: 1", failed=True)),
    ]:
        loop.tools.register(CodingTool(name, result), replace=True)
    metadata = {}
    session_key = "cli:coding-r2"

    def req(text):
        return TurnRequest(
            origin=Origin.USER,
            source=Source(channel="cli", chat_id="coding-r2", sender_id="user", chat_type=ChatType.DM),
            text=text,
        )

    try:
        await loop._process_message(
            req("Implement retry handling.\nDo not add dependencies or change the public API."), session_key=session_key
        )
        session = loop.sessions.get_or_create(session_key)
        original_history = copy.deepcopy(session.messages)
        loop._make_token_budget = lambda *args, **kwargs: budget(5000)
        result = await loop._process_message(
            req("Continue fixing the bug; do not declare completion."),
            session_key=session_key,
            context_metadata_sink=metadata,
        )
        persisted = copy.deepcopy(session.messages)
    finally:
        await loop.close()
    return {
        "executions": executions,
        "original_history": original_history,
        "metadata": metadata,
        "provider_context": provider.main_calls[-1],
        "continuation": result[0],
        "persisted": persisted,
        "summary_calls": provider.summary_calls,
    }


@pytest.mark.asyncio
async def test_g_tools_actually_execute_then_compacted_agent_continues(tmp_path):
    scenario = await run_coding_scenario(tmp_path)
    assert [name for name, _ in scenario["executions"]] == ["read_file", "write_file", "exec"]
    assert scenario["summary_calls"] == 1
    context = json.dumps(scenario["provider_context"])
    for expected in [
        "Do not add dependencies",
        "app.py",
        "pytest failed",
        "Next: fix retry count",
        "do not declare completion",
    ]:
        assert expected in context
    assert "tests remain failed" in scenario["continuation"]
    assert scenario["persisted"][: len(scenario["original_history"])] == scenario["original_history"]
    assert scenario["metadata"]["provider_context_validation"]["ok"] is True


@pytest.mark.asyncio
async def test_f_auto_summary_structured_grounded_and_within_budget(tmp_path):
    history = coding_history()
    original = copy.deepcopy(history)
    result, provider, builder = await assemble(tmp_path, history, 1600)
    assert stages(result)[-1] == "auto_compaction"
    assert result.metadata["context_estimate"]["before"] == result.metadata["compression_stages"][0]["before"]
    assert provider.summary_calls == 1
    text = result.messages[0]["content"]
    for evidence in [
        "Do not add dependencies",
        "app.py",
        "ERROR: pytest failed",
        "Next: fix retry count",
        "Current Blockers:",
    ]:
        assert evidence in text
    assert result.metadata["context_estimate"]["after"] <= 1600
    assert result.metadata["validation"]["structural_validated"] is True
    assert result.metadata["validation"]["budget_validated"] is True
    assert history == original
    assert set(builder.archive.read_working_state("r2")["compression"]["summary"]) == set(SUMMARY_FIELDS)


@pytest.mark.asyncio
async def test_g_real_agentloop_continues_failed_coding_task_and_persists_session(tmp_path):
    provider = Provider()
    loop = AgentLoop(
        provider=provider,
        workspace=tmp_path,
        model="fake-main",
        context_window_tokens=100_000,
        context_config=ContextConfig(protect_first_n=0, runtime_margin_tokens=0),
    )
    builder = next(b for b in loop.context_engine._phase_b if b.name == "curator")
    # Controlled fixed budget while still using the real factory/assembler/loop.
    initial = loop._make_token_budget
    loop._make_token_budget = lambda *args, **kwargs: budget(5000)
    session = loop.sessions.get_or_create("cli:r2")
    session.messages.extend(coding_history())
    loop.sessions.save(session)
    evidence = copy.deepcopy(session.messages)
    metadata = {}
    try:
        result = await loop._process_message(
            TurnRequest(
                origin=Origin.USER,
                source=Source(channel="cli", chat_id="r2", sender_id="user", chat_type=ChatType.DM),
                text="Continue fixing the retry bug; do not declare completion.",
            ),
            session_key="cli:r2",
            context_metadata_sink=metadata,
        )
    finally:
        await loop.close()
    assert provider.summary_calls == 1
    assert len(provider.main_calls) == 1
    context = json.dumps(provider.main_calls[0])
    for expected in [
        "Do not add dependencies",
        "public API",
        "app.py",
        "pytest failed",
        "Next: fix",
        "do not declare completion",
    ]:
        assert expected in context
    assert "tests remain failed" in result[0]
    assert session.messages[: len(evidence)] == evidence
    assert session.messages[-2]["role"] == "user"
    assert session.messages[-1]["role"] == "assistant"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["invalid", "timeout", "error", "hallucination", "oversized"])
async def test_h_summary_failure_never_sends_overbudget_context_or_mutates_session(tmp_path, mode):
    history = coding_history()
    original = copy.deepcopy(history)
    provider = Provider(mode)
    with pytest.raises(ContextBudgetError):
        await assemble(tmp_path, history, 1000, provider=provider, curator_timeout_seconds=0.02)
    assert provider.summary_calls == 1
    assert provider.main_calls == []
    assert history == original
    assert not list((tmp_path / "memory/.curator/working_state").glob("*.json"))
    traces = list((tmp_path / "memory/.curator/traces/r2").glob("*.jsonl"))
    assert '"accepted": false' in traces[0].read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_h_protected_user_text_alone_exceeds_budget(tmp_path):
    provider = Provider()
    with pytest.raises(ContextBudgetError):
        await assemble(tmp_path, [{"role": "user", "content": "original constraint " * 2000}], 500, provider=provider)
    assert provider.summary_calls == 0


def test_h_archive_retrieval_cannot_escape_store(tmp_path):
    outside = tmp_path / "secret.jsonl"
    outside.write_text('{}\n{"message":{"content":"private"}}', encoding="utf-8")
    archive = CuratorArchiveStore(tmp_path, ContextConfig())
    result = archive.retrieve([str(outside)])
    assert result["results"][0]["error"] == "outside curator archive"


def test_h_summary_cannot_remove_negation_by_quoting_substring():
    candidate = {field: [] for field in SUMMARY_FIELDS}
    candidate["test_results"] = [{"message_id": 0, "quote": "tests passed"}]
    with pytest.raises(ValueError, match="ungrounded_summary"):
        CuratorCompression.validate_summary(candidate, {"0": "No tests passed; work remains unfinished."})
    candidate["test_results"] = [{"message_id": 0, "quote": "tests passed"}]
    with pytest.raises(ValueError, match="ungrounded_summary"):
        CuratorCompression.validate_summary(
            candidate, {"0": "tests passed"}, full_sources={"0": "tests passed only if the pending fix is applied"}
        )


@pytest.mark.asyncio
async def test_live_stage1_restores_raw_result_before_session_save(tmp_path):
    raw = "source data " * 2000 + "\nRETRIEVED-MIDDLE\n" + "source data " * 3000

    class LongTool(Tool):
        name = "r2_long_tool"
        description = "deterministic long output"
        parameters = {"type": "object", "properties": {}}

        async def execute(self, **kwargs):
            return raw

    class LiveProvider(Provider):
        async def chat(self, messages, tools=None, **kwargs):
            self.main_calls.append(copy.deepcopy(messages))
            if not any(msg.get("role") == "tool" for msg in messages):
                return LLMResponse(
                    content="", tool_calls=[ToolCallRequest(id="live", name="r2_long_tool", arguments={})]
                )
            if not any(msg.get("name") == "read_context_archive" for msg in messages):
                observation = next(msg for msg in messages if msg.get("tool_call_id") == "live")
                match = re.search(r"Archive: (.+?); message_id=(\d+)", observation["content"])
                assert match
                return LLMResponse(
                    content="",
                    tool_calls=[
                        ToolCallRequest(
                            id="recover",
                            name="read_context_archive",
                            arguments={
                                "archive_refs": [match[1]],
                                "message_ids": [int(match[2])],
                                "offset_chars": len("source data ") * 2000 - 200,
                            },
                        )
                    ],
                )
            return LLMResponse(content="result inspected")

    provider = LiveProvider()
    loop = AgentLoop(provider=provider, workspace=tmp_path, context_config=ContextConfig(runtime_margin_tokens=0))
    loop.tools.register(LongTool())
    metadata = {}
    session = loop.sessions.get_or_create("cli:live")
    loop._make_token_budget = lambda *args, **kwargs: budget(5000)
    try:
        await loop._process_message(
            TurnRequest(
                origin=Origin.USER,
                source=Source(channel="cli", chat_id="live", sender_id="user", chat_type=ChatType.DM),
                text="read output",
            ),
            session_key="cli:live",
            context_metadata_sink=metadata,
        )
    finally:
        await loop.close()
    live_tool = next(msg for msg in provider.main_calls[-1] if msg.get("tool_call_id") == "live")
    assert "Archive:" in live_tool["content"] and len(live_tool["content"]) < len(raw)
    assert HistoryTrimmer.structural_errors(provider.main_calls[-1]) == []
    stored = next(msg for msg in session.messages if msg.get("tool_call_id") == "live")
    # Existing Session persistence has its own cap; R2 must not replace it.
    assert "source data" in stored["content"] and "Tool observation compacted" not in stored["content"]
    assert metadata["runtime_compression_stages"][0]["stage"] == "tool_result_offloading"
    recovered = next(msg for msg in provider.main_calls[-1] if msg.get("tool_call_id") == "recover")
    assert "RETRIEVED-MIDDLE" in recovered["content"]
    assert recovered["role"] == "tool"
    assert metadata["provider_context_validation"]["ok"] is True


@pytest.mark.asyncio
async def test_summary_projection_reused_across_builder_restart_and_append(tmp_path):
    provider = Provider()
    history = coding_history()
    first, _, _ = await assemble(tmp_path, history, 1600, provider=provider)
    assert provider.summary_calls == 1
    history += [{"role": "user", "content": "Continue."}, {"role": "assistant", "content": "still debugging"}]
    second, _, _ = await assemble(tmp_path, history, 1600, provider=provider)
    assert provider.summary_calls == 1
    assert stages(second) == []
    assert "Next: fix retry count" in second.messages[0]["content"]


@pytest.mark.asyncio
async def test_cache_invalidates_when_source_changes_and_malformed_cache_is_ignored(tmp_path):
    history = coding_history()
    provider = Provider()
    first, _, builder = await assemble(tmp_path, history, 1600, provider=provider)
    history[0]["content"] += "\nDo not rename functions."
    second, _, _ = await assemble(tmp_path, history, 1600, provider=provider)
    assert provider.summary_calls == 2
    assert "Do not rename functions." in second.messages[0]["content"]
    working = builder.archive.read_working_state("r2")
    working["compression"]["tool_projection"] = {"bad-id": "invalid"}
    builder.archive.write_working_state("r2", working)
    await assemble(tmp_path, history, 1600, provider=provider)
    assert provider.summary_calls == 3


@pytest.mark.asyncio
async def test_archive_reader_scoped_to_executing_session_and_preserves_structure(tmp_path):
    from looprail.agent.tools.execution import ToolExecutionContext
    from looprail.context_engine.curator import ContextArchiveReadTool

    store = CuratorArchiveStore(tmp_path, ContextConfig())
    history = [{"role": "user", "content": "source"}, *exchange("archived", "hello")]
    manifest = store.build_manifest("owner", history)
    ref = store.archive_messages("owner", manifest, history, list(range(3)))["archive_refs"][0]
    reader = ContextArchiveReadTool(store)
    denied = json.loads(
        await reader.execute_with_context(ToolExecutionContext(session_key="other"), archive_refs=[ref])
    )
    assert denied["results"][0]["error"] == "different session"
    restored = json.loads(
        await reader.execute_with_context(ToolExecutionContext(session_key="owner"), archive_refs=[ref], mode="exact")
    )
    messages = [item["message"] for item in restored["results"][0]["messages"]]
    assert HistoryTrimmer.structural_errors(messages) == []
    assert messages == history


@pytest.mark.asyncio
async def test_final_gate_rejects_strategy_expansion_before_main_provider(tmp_path):
    class ExpandingStrategy:
        async def before_llm_call(self, messages, tools, model):
            return [{"role": "system", "content": "expansion " * 10000}, *messages[1:]], tools, model

    provider = Provider()
    loop = AgentLoop(provider=provider, workspace=tmp_path, strategies=ExpandingStrategy())
    loop._make_token_budget = lambda *args, **kwargs: budget(6000)
    try:
        with pytest.raises(ContextBudgetError, match="provider_context_overflow_after_strategies"):
            await loop._process_message(
                TurnRequest(
                    origin=Origin.USER,
                    source=Source(channel="cli", chat_id="guard", sender_id="user", chat_type=ChatType.DM),
                    text="short task",
                )
            )
    finally:
        await loop.close()
    assert provider.main_calls == []


@pytest.mark.asyncio
async def test_exhaustion_synthesis_cannot_bypass_provider_budget(tmp_path):
    provider = Provider()
    loop = AgentLoop(provider=provider, workspace=tmp_path)
    try:
        result = await loop._synthesize_final_on_exhaustion(
            [{"role": "system", "content": "system"}, {"role": "user", "content": "long request " * 1000}],
            "fake-main",
            None,
            context_budget=budget(100),
            static_fallback="Task remains incomplete.",
        )
    finally:
        await loop.close()
    assert result == "Task remains incomplete."
    assert provider.main_calls == []


@pytest.mark.asyncio
async def test_all_stages_follow_priority_order_and_each_adopted_stage_reduces_tokens(tmp_path):
    history = [
        {"role": "user", "content": "Implement retry handling.\nDo not add dependencies."},
        *exchange("huge", "oversized result " * 2000),
        {"role": "assistant", "content": "intermediate analysis " * 300},
        {"role": "assistant", "content": "Decision: retain the API."},
        {"role": "user", "content": "low value task"},
        {"role": "assistant", "content": "redundant explanation " * 300},
    ]
    low_ids = [len(history) - 2, len(history) - 1]
    for i in range(5):
        history.extend(
            [
                {"role": "user", "content": f"inspect subtask {i}"},
                *exchange(f"old-{i}", "repeated observation " * 250),
                {"role": "assistant", "content": "subtask conclusion"},
            ]
        )
    history.extend(
        [
            {"role": "user", "content": "recent diagnosis"},
            {"role": "assistant", "content": "debug details " * 800 + "\nNext: fix app.py and rerun pytest."},
            {"role": "user", "content": "last inspection"},
            {"role": "assistant", "content": "more debug details " * 800 + "\nNext: keep working."},
        ]
    )
    result, provider, _ = await assemble(tmp_path, history, 2200, relevance={mid: 0.1 for mid in low_ids})
    names = list(dict.fromkeys(stages(result)))
    assert names == ["tool_result_offloading", "history_snip", "microcompaction", "context_collapse", "auto_compaction"]
    assert all(event["after"] < event["before"] for event in result.metadata["compression_stages"])
    assert provider.summary_calls == 1
    assert HistoryTrimmer.structural_errors(result.messages) == []
