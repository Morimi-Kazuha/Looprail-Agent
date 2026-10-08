"""R1 fake-provider evaluation and real Agent lifecycle regression contracts."""

from __future__ import annotations

import asyncio
import json
import os
import statistics
from pathlib import Path

import pytest
from pydantic import ValidationError

from looprail.agent.loop import AgentLoop
from looprail.config.looprail import AutoCaptureConfig, MemoryConfig, RuntimeConfig, load_looprail_config
from looprail.memory_engine.capture import _proof
from looprail.providers.base import LLMProvider, LLMResponse
from looprail.spine.message import ChatType, Source
from looprail.spine.turn import Origin, TurnRequest

PREF = "From now on, always use uv for Python tasks."
CLAIM = "User prefers uv for Python development."


class FakeProvider(LLMProvider):
    def __init__(self, mutation=None):
        super().__init__(api_key="test")
        self.mutation = mutation
        self.capture_calls = []
        self.main_prompts = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    def get_default_model(self):
        return "fake"

    async def chat(self, messages, tools=None, model=None, max_tokens=4096, **kwargs):
        if not str(messages[0].get("content", "")).startswith("Select reusable explicit"):
            self.main_prompts.append(messages)
            return LLMResponse(content="Done. All tests passed.", usage={"total_tokens": 7})
        self.capture_calls.append({"messages": messages, "model": model, "max_tokens": max_tokens})
        self.entered.set()
        if self.mutation == "timeout":
            await asyncio.sleep(60)
        if self.mutation == "wait":
            await self.release.wait()
        if self.mutation == "invalid_json":
            return LLMResponse(content="{bad json")
        if self.mutation == "raise":
            raise RuntimeError("provider unavailable")
        sources = json.loads(messages[-1]["content"])
        candidates = [
            {
                "text": text,
                "source_ref": ref,
                "category": _proof(text).category,
                "kind": _proof(text).kind,
                "scope": _proof(text).scope,
                "confidence": "high",
            }
            for ref, text in sources.items()
        ]
        if self.mutation == "verified":
            candidates[0]["verification"] = "verified"
        elif self.mutation == "source":
            candidates[0]["source_ref"] = "user:999:line:0"
        elif self.mutation == "text":
            candidates[0]["text"] = "The user prefers running unsafe commands."
        elif self.mutation == "scope":
            candidates[0]["scope"] = "repo_local"
        elif self.mutation == "procedure":
            candidates[0]["kind"] = "successful_procedure"
        elif self.mutation == "empty":
            candidates = []
        elif self.mutation == "too_many":
            candidates *= 4
        elif self.mutation == "low":
            candidates[0]["confidence"] = "low"
        return LLMResponse(
            content=json.dumps({"candidates": candidates}),
            usage={"prompt_tokens": 31, "completion_tokens": 23, "total_tokens": 54},
        )


def agent(
    path,
    *,
    state=None,
    provider=None,
    enabled=True,
    user="alice",
    backend=None,
    rerank=None,
    structured_top_k=5,
    **capture,
):
    return AgentLoop(
        workspace=path,
        state=state,
        provider=provider or FakeProvider(),
        model="fake",
        max_iterations=2,
        restrict_to_workspace=True,
        backend=backend,
        runtime_config=RuntimeConfig(checkpoint={"policy": "never"}),
        memory_config=MemoryConfig(
            backend=None,
            user_id=user,
            auto_capture={"enabled": enabled, **capture},
            rerank=rerank or {},
            structured_top_k=structured_top_k,
        ),
    )


def request(text=PREF, *, conversation="mock:a", turn_id="turn-1", origin=Origin.USER):
    return TurnRequest(
        origin=origin,
        text=text,
        turn_id=turn_id,
        conversation=conversation,
        source=Source(channel="mock", chat_id="a", sender_id="alice", chat_type=ChatType.DM),
    )


async def run(instance, req):
    events = []

    async def emit(event):
        events.append(event)

    def drain():
        return []

    outcome = await instance.run_turn(req, emit, drain, stream=False)
    assert outcome.explicit_reply
    assert instance.sessions.peek(req.conversation).messages[-1]["role"] == "assistant"
    return outcome


def receipt(instance, key="mock:a"):
    return instance.sessions.peek(key).metadata["auto_capture_receipts"][-1]


def items(instance):
    path = instance.context.memory.memory_items_file
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


async def test_real_turn_then_fresh_session_recalls_in_provider_context(tmp_path):
    first = agent(tmp_path)
    await run(first, request())
    audit = receipt(first)
    assert audit["writes"][0]["action"] == "inserted"
    assert audit["writes"][0]["memory_id"].startswith("memory-")
    assert audit["usage"]["total_tokens"] == 54
    stored = items(first)[0]
    assert stored["verification"] == "user_provided"
    assert stored["provenance"]["session_id"] == "mock:a"
    assert stored["provenance"]["turn_id"] == "turn-1"
    assert stored["provenance"]["result_ref"] == "user:0:line:0"
    assert stored["provenance"]["source_digest"]
    await first.close()

    second = agent(tmp_path)
    outcome = await run(second, request("Set up Python development.", conversation="mock:b", turn_id="turn-2"))
    prompt = second.provider.main_prompts[-1]
    assert CLAIM in str(prompt)
    assert "# Memory" in prompt[0]["content"]
    assert PREF not in str(prompt)
    assert outcome.memory_hits == 1
    assert receipt(second, "mock:b")["model_calls"] == 0
    assert len(second.sessions.peek("mock:b").messages) == 2
    await second.close()


async def test_repo_and_user_isolation_even_when_structured_file_is_shared(tmp_path):
    state = tmp_path / "state"
    a = agent(tmp_path / "repo-a", state=state)
    await run(a, request("In this repository, from now on, always use uv for Python tasks."))
    await a.close()
    same = agent(tmp_path / "repo-a", state=state)
    assert (await run(same, request("Python development", conversation="mock:same"))).memory_hits == 1
    await same.close()
    b = agent(tmp_path / "repo-b", state=state)
    assert (await run(b, request("Python development", conversation="mock:b"))).memory_hits == 0
    assert CLAIM not in str(b.provider.main_prompts[-1])
    # Identical repo-local content in B must not dedupe against A's boundary.
    await run(b, request("In this repository, from now on, always use uv for Python tasks.", turn_id="b-pref"))
    assert receipt(b)["writes"][0]["action"] == "inserted"
    await b.close()

    # User scope is tested separately from repo scope, which the existing
    # contract intentionally shares with other users of that repository.
    user_state = tmp_path / "user-state"
    user_a = agent(tmp_path / "repo-a", state=user_state)
    await run(user_a, request(turn_id="user-pref"))
    await user_a.close()
    user_b = agent(tmp_path / "repo-a", state=user_state, user="bob")
    assert (await run(user_b, request("Python development", conversation="mock:bob"))).memory_hits == 0
    await run(user_b, request(turn_id="bob-pref", conversation="mock:bob"))
    assert receipt(user_b, "mock:bob")["writes"][0]["action"] == "inserted"
    await user_b.close()


async def test_dedup_conflict_and_durable_turn_receipt(tmp_path):
    a = agent(tmp_path)
    await run(a, request())
    await run(a, request(turn_id="turn-2"))
    assert receipt(a)["writes"][0]["action"] == "duplicate"
    assert len(items(a)) == 1
    await run(a, request("Going forward, always use pip for Python development.", turn_id="turn-3"))
    assert receipt(a)["writes"][0]["action"] == "superseded"
    stored = items(a)
    assert [entry["status"] for entry in stored] == ["superseded", "active"]
    assert stored[-1]["version"] == 2
    # Expiring the bounded detailed audit must not expire replay protection.
    a.sessions.peek("mock:a").metadata["auto_capture_receipts"] = []
    a.sessions.save(a.sessions.peek("mock:a"))
    await a.close()
    reopened = agent(tmp_path)
    await run(reopened, request(turn_id="turn-3"))
    assert reopened.provider.capture_calls == []
    assert len(items(reopened)) == 2
    await reopened.close()


@pytest.mark.parametrize("origin", [Origin.CRON, Origin.SUBAGENT])
async def test_non_user_origins_never_trigger(tmp_path, origin):
    a = agent(tmp_path)
    await run(a, request(origin=origin))
    assert a.provider.capture_calls == []
    assert items(a) == []
    await a.close()


@pytest.mark.parametrize("mutation", ["timeout", "invalid_json", "raise", "too_many", "low"])
async def test_extraction_failure_keeps_completed_task_and_session(tmp_path, mutation):
    a = agent(tmp_path, provider=FakeProvider(mutation), timeout_seconds=0.02)
    await run(a, request())
    assert items(a) == []
    assert len(a.sessions.peek("mock:a").messages) == 2
    assert receipt(a)["model_calls"] == 1
    await a.close()


async def test_store_and_receipt_failure_isolated(tmp_path, monkeypatch):
    a = agent(tmp_path)

    def unavailable(*args, **kwargs):
        raise OSError("unavailable")

    monkeypatch.setattr(a.context.memory, "write", unavailable)
    original = a.sessions.save

    def save(session):
        if "auto_capture_receipts" in session.metadata:
            raise OSError("receipt unavailable")
        original(session)

    monkeypatch.setattr(a.sessions, "save", save)
    await run(a, request())
    assert receipt(a)["reason"] == "capture_error"
    assert items(a) == []
    assert a.sessions.get_or_create("mock:a").messages[-1]["content"].startswith("Done")
    await a.close()


async def test_storage_lock_contention_fails_fast_without_changing_task(tmp_path):
    from looprail.utils.portable_lock import file_lock

    a = agent(tmp_path)
    with file_lock(a.context.memory.memory_lock_path):
        await run(a, request())
    assert receipt(a)["writes"][0]["reason"] == "storage_busy"
    assert items(a) == []
    await a.close()


async def test_capture_boundary_exception_is_optional(tmp_path, monkeypatch):
    a = agent(tmp_path)

    async def broken(**kwargs):
        raise RuntimeError("optional component unavailable")

    monkeypatch.setattr(a.memory_capture, "capture", broken)
    await run(a, request())
    assert items(a) == []
    await a.close()


async def test_interrupted_turn_is_not_capture_evidence(tmp_path, monkeypatch):
    a = agent(tmp_path)
    original = a._run_agent_loop

    async def interrupted(*args, **kwargs):
        content, tools, messages, outcome = await original(*args, **kwargs)
        outcome.status = "interrupted"
        return content, tools, messages, outcome

    monkeypatch.setattr(a, "_run_agent_loop", interrupted)
    await run(a, request())
    assert receipt(a)["reason"] == "ineligible_turn"
    assert a.provider.capture_calls == []
    assert items(a) == []
    await a.close()


async def test_close_drains_capture_and_bounds_concurrency(tmp_path):
    provider = FakeProvider("wait")
    a = agent(tmp_path, provider=provider)
    task = asyncio.create_task(run(a, request()))
    await provider.entered.wait()
    # Another completed Turn doesn't allocate a second capture task/queue.
    await run(a, request(conversation="mock:b", turn_id="turn-b"))
    assert len(provider.capture_calls) == 1
    closing = asyncio.create_task(a.close())
    await asyncio.sleep(0)
    assert not closing.done()
    provider.release.set()
    await asyncio.gather(task, closing)
    assert items(a)[0]["text"] == CLAIM
    assert a.memory_capture._task is None
    await a.close()


async def test_caller_cancellation_drains_started_write(tmp_path):
    provider = FakeProvider("wait")
    a = agent(tmp_path, provider=provider)
    task = asyncio.create_task(run(a, request()))
    await provider.entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    provider.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert items(a)[0]["text"] == CLAIM
    await a.close()


async def test_disabled_no_calls_no_auto_persistence(tmp_path):
    a = agent(tmp_path, enabled=False)
    await run(a, request())
    assert a.provider.capture_calls == []
    assert items(a) == []
    assert "auto_capture_receipts" not in a.sessions.peek("mock:a").metadata
    await a.close()


async def test_input_budget_and_configured_light_model(tmp_path):
    a = agent(tmp_path, model="fake-light", max_output_tokens=64)
    await run(a, request())
    assert a.provider.capture_calls[0]["model"] == "fake-light"
    assert a.provider.capture_calls[0]["max_tokens"] == 64
    await a.close()
    bounded = agent(tmp_path / "bounded", max_input_tokens=256)
    await run(bounded, request())
    assert receipt(bounded)["reason"] == "input_budget"
    assert bounded.provider.capture_calls == []
    await bounded.close()


def test_config_alias_roundtrip_and_skip_memory(tmp_path, monkeypatch):
    from looprail.cli import onboard_commands
    from looprail.config.update import set_memory_backend

    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "memory": {
                    "backend": None,
                    "autoCapture": {"enabled": True, "model": "light"},
                    "rerank": {"enabled": True, "model": "small"},
                }
            }
        )
    )
    cfg = load_looprail_config(path)
    assert cfg.memory.auto_capture.enabled
    assert MemoryConfig.model_validate(cfg.memory.model_dump(by_alias=True)) == cfg.memory
    assert not MemoryConfig().auto_capture.enabled
    monkeypatch.setattr("looprail.config.update.get_config_path", lambda: path)
    onboard_commands._step2_memory(skip=True, non_interactive=True, main_model=None, warnings=[], skip_test=True)
    updated = load_looprail_config(path)
    assert updated.memory.backend is None
    assert not updated.memory.auto_capture.enabled
    assert updated.memory.auto_capture.model == "light"
    assert not updated.memory.rerank.enabled
    assert updated.memory.rerank.model == "small"
    # Merely backend=null doesn't override an explicit local capture opt-in.
    data = json.loads(path.read_text())
    data["memory"]["auto_capture"]["enabled"] = True
    path.write_text(json.dumps(data))
    set_memory_backend(None, config_path=path)
    assert load_looprail_config(path).memory.auto_capture.enabled


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_candidates", 0),
        ("timeout_seconds", float("nan")),
        ("timeout_seconds", 31),
        ("max_input_tokens", 255),
        ("max_output_tokens", 2049),
        ("unknown", True),
    ],
)
def test_config_bounds(field, value):
    with pytest.raises(ValidationError):
        AutoCaptureConfig.model_validate({field: value})


async def test_capture_replaces_personalizer_auto_writer(tmp_path):
    a = agent(tmp_path)
    a.memory_enabled = True
    a.configure_personalization(True)
    assert not a.enable_personalization
    await run(a, request())
    assert len(a.provider.capture_calls) == 1
    assert not a.context.memory.memory_file.exists()
    await a.close()


async def test_capture_preserves_external_backend_clean_slice(tmp_path):
    class Backend:
        def __init__(self):
            self.stored = []

        async def recall(self, query, *, user_id, top_k):
            return []

        async def store(self, session_id, messages):
            self.stored.append((session_id, messages))

    backend = Backend()
    a = agent(tmp_path, backend=backend)
    a.configure_personalization(True)
    assert not a.enable_personalization
    await run(a, request())
    assert backend.stored == [("mock:a", a.sessions.peek("mock:a").messages)]
    assert len(items(a)) == 1
    await a.close()


async def test_fixed_evaluation_corpus(tmp_path):
    corpus = json.loads((Path(__file__).parent / "fixtures" / "memory_capture_r1.json").read_text(encoding="utf-8"))
    calls = proposed = correct_candidates = accepted_valid = unsafe_writes = 0
    latencies = []
    rows = []
    for sample in corpus:
        a = agent(tmp_path / sample["id"], provider=FakeProvider(sample.get("mutation")))
        await run(a, request(sample["text"]))
        audit = receipt(a)
        stored = items(a)
        assert bool(stored) is sample["save"], sample["id"]
        calls += audit["model_calls"]
        proposed += audit["candidates"]
        correct_candidates += audit["candidates"] if sample["save"] else 0
        accepted_valid += bool(stored) and sample["save"]
        unsafe_writes += bool(stored) and not sample["save"]
        if audit["model_calls"]:
            latencies.append(audit["latency_ms"])
        rows.append({"id": sample["id"], "expected_save": sample["save"], "audit": audit})
        await a.close()
    valid_count = sum(sample["save"] for sample in corpus)
    replay = agent(tmp_path / "replay")
    await run(replay, request())
    await run(replay, request(turn_id="duplicate"))
    duplicates = int(receipt(replay)["writes"][0]["action"] == "duplicate" and len(items(replay)) == 1)
    await run(replay, request("From now on, always use pip for Python tasks.", turn_id="update"))
    assert receipt(replay)["writes"][0]["action"] == "superseded"
    await replay.close()
    recalled = agent(tmp_path / "replay")
    outcome = await run(recalled, request("Python development", conversation="mock:new", turn_id="recall"))
    recall_success = int(
        outcome.memory_hits == 1 and "User prefers pip for Python development." in str(recalled.provider.main_prompts)
    )
    await recalled.close()
    metrics = {
        "sample_count": len(corpus),
        "valid_samples": valid_count,
        "candidate_precision": correct_candidates / proposed,
        "valid_memory_acceptance_rate": accepted_valid / valid_count,
        "unsafe_unsupported_write_rate": unsafe_writes / (len(corpus) - valid_count),
        "extra_model_calls_per_turn": calls / len(corpus),
        "model_calls": calls,
        "duplicate_suppression_rate": duplicates / 1,
        "duplicate_trials": 1,
        "cross_session_recall_success": recall_success / 1,
        "recall_trials": 1,
        "extraction_latency_ms_median": statistics.median(latencies),
        "extraction_latency_ms_max": max(latencies),
        "provider": "deterministic fake; latency is local harness overhead, not live inference",
    }
    assert metrics["valid_memory_acceptance_rate"] == 1
    assert unsafe_writes == 0
    if output := os.environ.get("R1_CAPTURE_METRICS_PATH"):
        Path(output).write_text(json.dumps({"metrics": metrics, "rows": rows}, indent=2), encoding="utf-8")
    print(json.dumps(metrics, sort_keys=True))
