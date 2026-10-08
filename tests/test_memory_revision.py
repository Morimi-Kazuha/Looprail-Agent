"""Four categories, source gates and small-model selection through real Turns."""

from __future__ import annotations

import asyncio
import json

import pytest

from looprail.config.looprail import MemoryRerankConfig
from looprail.memory_engine.backend import Memory, MemoryCategory, MemoryKind, MemoryVerification
from looprail.memory_engine.select import MemorySelector
from looprail.providers.base import LLMResponse
from tests.test_memory_auto_capture import FakeProvider, agent, items, receipt, request, run

SAMPLES = [
    ("Remember about me: I build Python developer tools.", "user", "user_fact", "user"),
    ("记住我的信息：我是 Python 开发者。", "user", "user_fact", "user"),
    ("Feedback for future tasks: Always use uv for Python tasks.", "feedback", "user_preference", "user"),
    ("纠正你的长期行为：不要在回复中重复完整日志。", "feedback", "user_preference", "user"),
    ("For this project, remember: This Python application manages local jobs.", "project", "project_fact", "project"),
    ("项目长期决策：采用 Python 开发本地任务管理器。", "project", "project_fact", "project"),
    (
        "Project reference: Python docs https://docs.python.org/3/tutorial/index.html",
        "reference",
        "reference",
        "repo_local",
    ),
    (
        "For my future tasks, use this reference: Python docs https://docs.python.org/3/tutorial/index.html",
        "reference",
        "reference",
        "user",
    ),
    (
        "In this repository, feedback for future tasks: Always use uv for Python tasks.",
        "feedback",
        "user_preference",
        "repo_local",
    ),
]


class SelectingProvider(FakeProvider):
    def __init__(self, mode="user"):
        super().__init__()
        self.mode = mode
        self.selection_calls = []

    async def chat(self, messages, tools=None, model=None, max_tokens=4096, **kwargs):
        if not messages[0]["content"].startswith("Select relevant existing memories"):
            return await super().chat(messages, tools=tools, model=model, max_tokens=max_tokens, **kwargs)
        payload = json.loads(messages[-1]["content"])
        self.selection_calls.append({"payload": payload, "model": model, "max_tokens": max_tokens})
        candidates = payload["candidates"]
        ids = [item["id"] for item in candidates if item["category"] == "user"][:1]
        if self.mode == "invent":
            ids = ["memory-does-not-exist"]
        elif self.mode == "duplicate":
            ids = [candidates[0]["id"], candidates[0]["id"]]
        elif self.mode == "empty":
            ids = []
        elif self.mode == "raise":
            raise RuntimeError("small model unavailable")
        elif self.mode == "timeout":
            await asyncio.sleep(60)
        elif self.mode == "json":
            return LLMResponse(content="not json")
        elif self.mode == "text":
            return LLMResponse(content=json.dumps({"memory_ids": ids, "text": "invented claim"}))
        return LLMResponse(content=json.dumps({"memory_ids": ids}), usage={"total_tokens": 12})


@pytest.mark.parametrize("text,category,kind,scope", SAMPLES)
async def test_four_categories_capture_scope_and_persistence(tmp_path, text, category, kind, scope):
    a = agent(tmp_path)
    await run(a, request(text))
    stored = items(a)
    assert len(stored) == 1
    item = stored[0]
    assert (item["category"], item["kind"], item["scope"]) == (category, kind, scope)
    assert item["verification"] == "user_provided"
    assert item["provenance"]["source_digest"] == item["provenance"]["evidence_digest"]
    assert receipt(a)["writes"][0]["accepted"]
    decoded = a.context.memory.read_memory_items()[0]
    assert decoded.category == category
    assert decoded.verification != MemoryVerification.VERIFIED.value
    await a.close()


@pytest.mark.parametrize("text", ["Please remember that I build Python tools.", "请记住，我是 Python 开发者。"])
async def test_natural_explicit_user_profile_statements(tmp_path, text):
    a = agent(tmp_path)
    await run(a, request(text))
    assert items(a)[0]["category"] == "user"
    assert items(a)[0]["kind"] == "user_fact"
    await a.close()


async def seed_four_categories(path, state=None):
    a = agent(path, state=state)
    for index in (0, 2, 4, 6):
        await run(a, request(SAMPLES[index][0], turn_id=f"seed-{index}"))
    assert len(items(a)) == 4
    await a.close()


async def test_session_a_capture_session_b_small_model_changes_selection(tmp_path):
    await seed_four_categories(tmp_path)
    provider = SelectingProvider()
    b = agent(
        tmp_path,
        provider=provider,
        structured_top_k=1,
        rerank={"enabled": True, "model": "fake-small", "candidate_top_k": 8},
    )
    deterministic = b.context.memory.recall_memory("Python", top_k=1)
    outcome = await run(b, request("Python", conversation="mock:b", turn_id="session-b"))
    assert len(provider.selection_calls) == 1
    call = provider.selection_calls[0]
    assert call["model"] == "fake-small"
    assert len(call["payload"]["candidates"]) == 4
    selected_id = next(item["id"] for item in call["payload"]["candidates"] if item["category"] == "user")
    assert selected_id != deterministic[0].memory_id
    prompt = str(provider.main_prompts[-1])
    assert "I build Python developer tools." in prompt
    assert "This Python application manages local jobs." not in prompt
    assert "https://docs.python.org/3/tutorial/index.html" not in prompt
    assert "Remember about me:" not in prompt
    assert outcome.memory_hits == 1
    assert len(b.sessions.peek("mock:b").messages) == 2
    diagnostics = b.context.memory.last_memory_diagnostics
    assert diagnostics["selected"] == diagnostics["selected_count"] == 1
    assert diagnostics["selected_ids"] == [selected_id]
    await b.close()


@pytest.mark.parametrize("mode", ["invent", "duplicate", "raise", "timeout", "json", "text"])
async def test_small_model_failure_or_invention_falls_back(tmp_path, mode):
    await seed_four_categories(tmp_path)
    provider = SelectingProvider(mode)
    b = agent(tmp_path, provider=provider, structured_top_k=1, rerank={"enabled": True, "timeout_seconds": 0.02})
    original = b.context.memory.recall_memory("Python", top_k=1)[0]
    outcome = await run(b, request("Python", conversation="mock:b"))
    assert len(provider.selection_calls) == 1
    assert original.text in str(provider.main_prompts[-1])
    assert "invented claim" not in str(provider.main_prompts[-1])
    assert outcome.memory_hits == 1
    await b.close()


async def test_valid_empty_selection_removes_structured_memory(tmp_path):
    await seed_four_categories(tmp_path)
    provider = SelectingProvider("empty")
    b = agent(tmp_path, provider=provider, rerank={"enabled": True})
    outcome = await run(b, request("Python", conversation="mock:b"))
    assert provider.selection_calls
    assert outcome.memory_hits == 0
    assert "I build Python developer tools." not in str(provider.main_prompts[-1])
    diagnostics = b.context.memory.last_memory_diagnostics
    assert diagnostics["selected"] == diagnostics["selected_count"] == 0
    assert diagnostics["selected_ids"] == []
    await b.close()


async def test_scopes_filter_before_model_sees_candidates(tmp_path):
    state = tmp_path / "state"
    await seed_four_categories(tmp_path / "repo-a", state=state)
    provider = SelectingProvider()
    # User info/feedback can cross repositories for the same configured user;
    # project facts and project reference links cannot.
    b = agent(tmp_path / "repo-b", state=state, provider=provider, rerank={"enabled": True})
    await run(b, request("Python", conversation="mock:b"))
    assert {item["category"] for item in provider.selection_calls[0]["payload"]["candidates"]} == {"user", "feedback"}
    await b.close()
    other = SelectingProvider()
    c = agent(tmp_path / "repo-b", state=state, user="bob", provider=other, rerank={"enabled": True})
    outcome = await run(c, request("Python", conversation="mock:bob"))
    assert not other.selection_calls
    assert outcome.memory_hits == 0
    await c.close()


@pytest.mark.parametrize(
    "text",
    [
        "Remember about me: Maybe I will become a Python developer.",
        "For this project, remember: An example might use Python.",
        "Project reference: https://docs.example.org/?token=private",
        "Project reference: https://user:password@docs.example.org/guide",
        "Model summary: This Python application manages local jobs.",
    ],
)
async def test_unsupported_declarations_never_auto_write(tmp_path, text):
    a = agent(tmp_path)
    await run(a, request(text))
    assert items(a) == []
    assert a.provider.capture_calls == []
    await a.close()


async def test_feedback_updates_existing_preference_without_parallel_active_claims(tmp_path):
    a = agent(tmp_path)
    await run(a, request("From now on, always give concise answers.", turn_id="old"))
    await run(a, request("Feedback for future tasks: Always give detailed answers.", turn_id="correction"))
    assert receipt(a)["writes"][0]["action"] == "superseded"
    assert [(item["category"], item["status"]) for item in items(a)] == [("user", "superseded"), ("feedback", "active")]
    await run(a, request("Feedback for future tasks: Always give detailed answers.", turn_id="repeat"))
    assert receipt(a)["writes"][0]["action"] == "duplicate"
    assert len(items(a)) == 2
    await a.close()


def test_generic_project_claim_cannot_gain_verification_from_category(tmp_path):
    a = agent(tmp_path)
    store = a.context.memory
    result = store.write(
        Memory(
            text="All tests passed.",
            kind="project_fact",
            category="project",
            scope="project",
            verification="user_provided",
        )
    )
    assert not result.accepted
    assert result.reason == "user_provided_only_for_preference"
    assert not store.write(
        Memory(
            text="All tests passed.", kind="project_fact", category="project", scope="project", verification="uncertain"
        )
    ).accepted
    assert not store.write(
        Memory(text="User profile.", kind="user_fact", category="project", scope="user", verification="observed")
    ).accepted
    assert items(a) == []


async def test_legacy_records_without_category_still_recall_and_dedupe(tmp_path):
    a = agent(tmp_path)
    await run(a, request())
    path = a.context.memory.memory_items_file
    legacy = items(a)[0]
    legacy.pop("category")
    path.write_text(json.dumps(legacy) + "\n", encoding="utf-8")
    assert a.context.memory.recall_memory("Python")[0].category is None
    await run(a, request(turn_id="repeat-legacy"))
    assert receipt(a)["writes"][0]["action"] == "duplicate"
    assert len(items(a)) == 1
    await a.close()


async def test_selector_budget_disable_and_no_candidates_skip_model():
    provider = SelectingProvider()
    candidates = [Memory(text="Python", memory_id="memory-existing", category="user")]
    for config, pool, expected in [
        (MemoryRerankConfig(), candidates, "disabled"),
        (MemoryRerankConfig(enabled=True), [], "no_candidates"),
        (MemoryRerankConfig(enabled=True, max_input_tokens=256), candidates, "input_budget"),
    ]:
        selected, audit = await MemorySelector(config, provider, "fake").select("Python", pool, 1)
        assert audit["reason"] == expected
        assert audit["model_calls"] == 0
    assert not provider.selection_calls


def test_category_enum_and_rerank_config_aliases():
    assert {item.value for item in MemoryCategory} == {"user", "feedback", "project", "reference"}
    config = MemoryRerankConfig.model_validate({"enabled": True, "candidateTopK": 4, "maxOutputTokens": 128})
    assert config.candidate_top_k == 4
    assert config.max_output_tokens == 128
    assert MemoryKind.PROJECT_FACT.value == "project_fact"
