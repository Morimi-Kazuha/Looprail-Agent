"""Contract tests for the existing structured Memory authority.

These tests deliberately use deterministic local inputs.  They exercise the
same ``MemoryStore`` that the shipping Context/Agent path owns; no model is
asked to extract or rank a fact.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from looprail.agent.loop import AgentLoop
from looprail.cli._plugin_stack import maybe_build_memory_backend
from looprail.cli.commands import app
from looprail.config.looprail import ContextConfig, LooprailConfig, MemoryConfig
from looprail.config.schema import Config
from looprail.context_engine import AssemblyContext, ContextAssembler, TurnContext, build_context_engine
from looprail.context_engine.segments.memory import MemorySegmentBuilder
from looprail.memory_engine import (
    Memory,
    MemoryKind,
    MemoryScope,
    MemoryStatus,
    MemoryVerification,
    TokenBudget,
)
from looprail.memory_engine.consolidate.consolidator import MemoryStore, repository_identity_for_path
from looprail.plugin import PluginNotFoundError, PluginRegistry
from looprail.providers.base import LLMProvider, LLMResponse
from looprail.spine.message import ChatType, Source
from looprail.spine.turn import Origin, TurnRequest


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 1, 1, 12, 0, 0)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **kwargs: int) -> None:
        from datetime import timedelta

        self.value += timedelta(**kwargs)


def _store(
    workspace: Path,
    *,
    repo: str = "repo-a",
    project: str | None = "project-a",
    user: str = "alice",
    clock: _Clock | None = None,
) -> MemoryStore:
    return MemoryStore(
        workspace,
        now_fn=clock or _Clock(),
        repository_id=repo,
        project_id=project,
        user_id=user,
    )


def _candidate(
    store: MemoryStore,
    text: str,
    *,
    kind: str = MemoryKind.REPO_CONVENTION.value,
    scope: str = MemoryScope.REPO_LOCAL.value,
    verification: str = MemoryVerification.VERIFIED.value,
    normalized_key: str | None = None,
    source: str = "repository-read",
    source_type: str = "repository_observation",
    metadata: dict | None = None,
    **provenance: str,
) -> Memory:
    evidence = {
        "source": source,
        "source_type": source_type,
        "session_id": "session-a",
        "turn_id": "turn-1",
        "repo_identity": store.repository_identity,
        "project_id": store.project_id,
        "user_id": store.user_id,
        "observed_at": "2026-01-01T12:00:00",
    }
    evidence.update(provenance)
    return Memory(
        text=text,
        kind=kind,
        scope=scope,
        verification=verification,
        normalized_key=normalized_key,
        metadata=metadata or {},
        provenance=evidence,
    )


def _budget() -> TokenBudget:
    return TokenBudget(
        context_length=100_000,
        reserved_output=4_000,
        reserved_tools=2_000,
        reserved_system=1_000,
        available_history=93_000,
    )


def _assembly_context(workspace: Path, message: str = "pytest convention") -> AssemblyContext:
    return AssemblyContext(
        session_key="test:memory",
        current_message=message,
        media=None,
        channel="test",
        chat_id="memory",
        session_messages=[],
        budget=_budget(),
    )


def test_A_write_policy_provenance_and_item_schema_are_explicit(tmp_path: Path) -> None:
    store = _store(tmp_path)
    result = store.write(
        _candidate(
            store,
            "This repository runs pytest tests under tests.",
            metadata={"labels": ["testing"], "raw_payload": "must not persist"},
        )
    )

    assert result.accepted is True
    assert result.action == "inserted"
    assert result.reason == "accepted"
    assert result.item is not None
    assert result.item.scope == MemoryScope.REPO_LOCAL.value
    assert result.item.verification == MemoryVerification.VERIFIED.value
    assert result.item.memory_id and result.item.content_digest
    assert result.item.provenance["repo_identity"] == "repo-a"
    assert result.item.provenance["session_id"] == "session-a"
    assert result.item.provenance["turn_id"] == "turn-1"
    assert result.item.metadata == {"labels": ["testing"]}

    record = json.loads(store.memory_items_file.read_text(encoding="utf-8").splitlines()[0])
    assert record["schema"] == "looprail.memory.item.v1"
    assert "raw_payload" not in record["metadata"]
    assert "messages" not in record
    assert "tool_result" not in record


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("verification", MemoryVerification.UNCERTAIN.value, "verification_not_eligible"),
        ("verification", MemoryVerification.DERIVED.value, "verification_not_eligible"),
        ("scope", MemoryScope.SESSION_LOCAL.value, "scope_not_durable"),
        ("kind", MemoryKind.UNCLASSIFIED.value, "kind_not_reusable"),
    ],
)
def test_B_unverified_or_task_local_candidates_are_excluded(
    tmp_path: Path,
    field: str,
    value: str,
    reason: str,
) -> None:
    store = _store(tmp_path)
    candidate = _candidate(store, "temporary claim about pytest")
    candidate = Memory(**{**candidate.__dict__, field: value})

    result = store.write(candidate)

    assert result.accepted is False
    assert result.action == "rejected"
    assert result.reason == reason
    assert store.read_memory_items() == []
    assert not store.memory_items_file.exists()


def test_B_raw_tool_and_recovery_evidence_never_become_durable_memory(tmp_path: Path) -> None:
    store = _store(tmp_path)
    raw_tool = store.write(
        _candidate(
            store,
            "pytest command output says everything passed",
            source_type="tool_result",
            tool_name="exec",
            result_ref="result-1",
        )
    )
    recovery = store.write(
        _candidate(
            store,
            "[Recovery — verify the interrupted turn before continuing]",
            source_type="recovery",
        )
    )

    assert raw_tool.reason == "source_not_durable"
    assert recovery.reason == "source_not_durable"
    store.append_history("[Recovery — UNKNOWN effect effect-1]")
    assert not store.history_file.exists()


def test_C_exact_dedupe_uses_scope_kind_key_source_and_digest(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.write(
        _candidate(
            store,
            "Run pytest from the repository root.",
            normalized_key="test command",
        )
    )
    duplicate = store.write(
        _candidate(
            store,
            "  Run pytest from the repository root.  ",
            normalized_key="test command",
        )
    )

    assert first.accepted is True
    assert duplicate.accepted is True
    assert duplicate.action == "duplicate"
    assert duplicate.duplicate_of == first.item.memory_id
    assert len(store.read_memory_items()) == 1


def test_D_conflict_supersession_and_explicit_invalidation_are_retrievable(tmp_path: Path) -> None:
    clock = _Clock()
    store = _store(tmp_path, clock=clock)
    old = store.write(
        _candidate(
            store,
            "Run pytest from the repository root.",
            normalized_key="test command",
        )
    )
    clock.advance(days=1)
    new = store.write(
        _candidate(
            store,
            "Run pytest -q from the repository root.",
            normalized_key="test command",
        )
    )

    assert old.item is not None and new.item is not None
    assert new.action == "superseded"
    assert new.superseded_ids == (old.item.memory_id,)
    assert new.item.supersedes == old.item.memory_id
    assert new.item.version == 2

    records = {item.memory_id: item for item in store.read_memory_items()}
    assert records[old.item.memory_id].status == MemoryStatus.SUPERSEDED.value
    assert records[old.item.memory_id].superseded_by == new.item.memory_id
    assert [item.text for item in store.recall_memory("pytest command")] == [new.item.text]

    invalidated = store.invalidate_memory(new.item.memory_id, "configuration changed")
    assert invalidated.accepted is True
    assert invalidated.item is not None
    assert invalidated.item.status == MemoryStatus.INVALIDATED.value
    assert store.recall_memory("pytest command") == []
    reintroduced = store.write(
        _candidate(
            store,
            "Run pytest -q from the repository root.",
            normalized_key="test command",
        )
    )
    assert reintroduced.action == "duplicate"
    assert reintroduced.reason == "duplicate_inactive"
    assert len(store.read_memory_items()) == 2

    tombstone = store.write(_candidate(store, "pytest cache is disposable", normalized_key="cache"))
    assert tombstone.item is not None
    tombstoned = store.invalidate_memory(tombstone.item.memory_id, "cache policy", tombstone=True)
    assert tombstoned.action == "tombstoned"
    assert tombstoned.item is not None
    assert tombstoned.item.status == MemoryStatus.TOMBSTONED.value

    update_store = _store(tmp_path / "update")
    original = update_store.write(_candidate(update_store, "pytest is the runner", normalized_key="runner"))
    assert original.item is not None
    updated = update_store.update_memory(original.item.memory_id, text="pytest -q is the runner")
    assert updated.accepted is True
    assert updated.action == "superseded"
    assert updated.item is not None and updated.item.version == 2


def test_E_cross_session_reuse_and_cross_repository_isolation(tmp_path: Path) -> None:
    store_a = _store(tmp_path, repo="repo-a", project="project-a", user="alice")
    accepted = store_a.write(
        _candidate(store_a, "The repository uses pytest for validation.", normalized_key="test runner")
    )
    assert accepted.accepted is True

    fresh_session = _store(tmp_path, repo="repo-a", project="project-a", user="alice")
    assert [item.text for item in fresh_session.recall_memory("pytest validation")] == [
        "The repository uses pytest for validation."
    ]

    other_repo = _store(tmp_path, repo="repo-b", project="project-b", user="alice")
    assert other_repo.recall_memory("pytest validation") == []
    assert other_repo.last_memory_diagnostics["excluded"]["wrong_scope"] == 1

    other_user = _store(tmp_path, repo="repo-a", project="project-a", user="bob")
    user_item = other_user.write(
        _candidate(
            other_user,
            "Alice prefers concise answers.",
            kind=MemoryKind.USER_PREFERENCE.value,
            scope=MemoryScope.USER.value,
            verification=MemoryVerification.USER_PROVIDED.value,
            normalized_key="answer style",
        )
    )
    assert user_item.accepted is True
    assert _store(tmp_path, repo="repo-a", project="project-a", user="alice").recall_memory("concise") == []


def test_F_recall_is_bounded_deterministic_and_marks_stale_sources(tmp_path: Path) -> None:
    source = tmp_path / "config.toml"
    source.write_text("[tool]", encoding="utf-8")
    store = _store(tmp_path)
    first = store.write(
        _candidate(
            store,
            "pytest reads the config file",
            normalized_key="config source",
            source_path="config.toml",
        )
    )
    second = store.write(
        _candidate(store, "pytest uses a deterministic runner", normalized_key="runner")
    )
    assert first.accepted and second.accepted

    hits = store.recall_memory("pytest", top_k=1, max_chars=64)
    assert len(hits) == 1
    assert hits == store.recall_memory("pytest", top_k=1, max_chars=64)
    assert store.recall_memory("pytest", top_k=0) == []
    assert store.last_memory_diagnostics["warnings"] == ["bounded_empty_request"]

    source.unlink()
    stale_hits = store.recall_memory("pytest", top_k=5, max_chars=500)
    assert all("config file" not in item.text for item in stale_hits)
    assert store.last_memory_diagnostics["excluded"]["stale:source_missing"] == 1


@pytest.mark.asyncio
async def test_G_existing_memory_segment_reaches_context_assembler(tmp_path: Path) -> None:
    store = _store(tmp_path)
    result = store.write(
        _candidate(store, "This project requires pytest under tests.", normalized_key="test layout")
    )
    assert result.accepted

    segment_builder = MemorySegmentBuilder(
        store,
        backend=None,
        project_id="project-a",
        user_id="alice",
        structured_top_k=3,
        structured_max_chars=200,
        enabled=True,
    )
    segment = await segment_builder.build(_assembly_context(tmp_path))
    assert segment is not None
    assert "This project requires pytest under tests." in segment.text
    assert segment.meta["memory_hits"] == 1
    assert segment.meta["structured_memory_hits"] == 1
    assert segment.meta["structured_memory_diagnostics"]["selected"] == 1

    assembled = await ContextAssembler([segment_builder], lambda: []).assemble(
        "test:memory",
        [],
        _budget(),
        turn=TurnContext(current_message="pytest tests"),
    )
    assert "This project requires pytest under tests." in assembled.messages[0]["content"]
    assert assembled.metadata["structured_memory_hits"] == 1


def test_H_recovery_only_and_transient_failure_inputs_do_not_contaminate_store(tmp_path: Path) -> None:
    store = _store(tmp_path)
    transient = store.write(
        _candidate(
            store,
            "The command failed once because the network was unavailable.",
            metadata={"transient": True},
        )
    )
    hypothesis = store.write(
        _candidate(
            store,
            "Maybe pytest is configured somewhere else.",
            metadata={"hypothesis": True},
        )
    )
    recovery = store.write(
        _candidate(
            store,
            "checkpoint: inspect the unknown effect before continuing",
            source_type="checkpoint",
        )
    )

    assert transient.reason == "not_reusable"
    assert hypothesis.reason == "not_reusable"
    assert recovery.reason == "source_not_durable"
    assert store.recall_memory("network pytest") == []
    assert store.read_memory_items() == []


def test_I_corrupt_schema_retrieval_is_fail_closed_and_explainable(tmp_path: Path) -> None:
    store = _store(tmp_path)
    accepted = store.write(_candidate(store, "pytest is the test runner", normalized_key="runner"))
    assert accepted.accepted
    with store.memory_items_file.open("a", encoding="utf-8") as handle:
        handle.write('{"schema":"not-a-memory-item","text":"poison"}\n')

    hits = store.recall_memory("pytest", top_k=5, max_chars=500)
    assert [item.text for item in hits] == ["pytest is the test runner"]
    assert any("line_" in warning for warning in store.last_memory_diagnostics["warnings"])

    rejected_write = store.write(_candidate(store, "pytest uses a runner", normalized_key="other"))
    assert rejected_write.accepted is False
    assert rejected_write.action == "error"
    assert rejected_write.reason == "store_corrupt"

    empty = _store(tmp_path / "empty")
    assert empty.recall_memory("anything") == []
    assert empty.last_memory_diagnostics["candidates"] == 0
    assert empty.last_memory_diagnostics["selected"] == 0


def test_J_write_and_recall_results_expose_bounded_explainability(tmp_path: Path) -> None:
    store = _store(tmp_path)
    result = store.write(_candidate(store, "pytest is used for validation", normalized_key="validation"))
    assert result.to_dict()["accepted"] is True
    assert result.to_dict()["action"] == "inserted"
    assert result.to_dict()["reason"] == "accepted"
    assert "text" not in result.to_dict()
    assert "diagnostics" in result.to_dict()

    store.recall_memory("pytest validation")
    diagnostics = store.last_memory_diagnostics
    assert set(
        (
            "query",
            "scope",
            "backend",
            "candidates",
            "eligible_count",
            "selected",
            "selected_count",
            "stale_excluded",
            "budget",
            "excluded",
            "warnings",
        )
    ).issubset(diagnostics)
    assert "pytest is used for validation" not in json.dumps(diagnostics)


class _CapturingProvider(LLMProvider):
    def __init__(self) -> None:
        super().__init__(api_key="test")
        self.seen_messages: list[dict] = []

    async def chat(
        self,
        messages,
        tools=None,
        model=None,
        max_tokens=4096,
        temperature=0.7,
        reasoning_effort=None,
        tool_choice=None,
    ) -> LLMResponse:
        self.seen_messages = list(messages)
        return LLMResponse(content="done", finish_reason="stop")

    def get_default_model(self) -> str:
        return "stub"


def _request(text: str, chat_id: str) -> TurnRequest:
    return TurnRequest(
        origin=Origin.USER,
        source=Source(channel="test", chat_id=chat_id, sender_id="alice", chat_type=ChatType.DM),
        text=text,
    )


@pytest.mark.asyncio
async def test_J_real_agentloop_mainline_reuses_same_repo_and_excludes_other_repo(tmp_path: Path) -> None:
    repo_a = tmp_path / "repo-a"
    repo_a.mkdir()
    config = MemoryConfig(backend=None, user_id="alice", project_id="project-a")

    provider_a = _CapturingProvider()
    agent_a = AgentLoop(
        provider=provider_a,
        workspace=repo_a,
        state=repo_a,
        model="stub",
        backend=None,
        memory_config=config,
        max_iterations=1,
        restrict_to_workspace=True,
    )
    accepted = agent_a.context.memory.write(
        Memory(
            text="This repository uses pytest tests under tests.",
            kind=MemoryKind.REPO_CONVENTION.value,
            scope=MemoryScope.REPO_LOCAL.value,
            verification=MemoryVerification.VERIFIED.value,
            normalized_key="test layout",
            provenance={
                "source": "read-config",
                "source_type": "repository_observation",
                "session_id": "session-a",
                "turn_id": "turn-a",
                "repo_identity": agent_a.context.repository_identity,
                "project_id": "project-a",
                "user_id": "alice",
            },
        )
    )
    assert accepted.accepted is True
    assert accepted.item is not None
    assert accepted.item.provenance["repo_identity"] == agent_a.context.repository_identity
    await agent_a._process_message(_request("pytest tests", "session-a"))
    assert "This repository uses pytest tests under tests." in "\n".join(
        str(message.get("content")) for message in provider_a.seen_messages
    )
    await agent_a.close()

    provider_b = _CapturingProvider()
    agent_b = AgentLoop(
        provider=provider_b,
        workspace=repo_a,
        state=repo_a,
        model="stub",
        backend=None,
        memory_config=config,
        max_iterations=1,
        restrict_to_workspace=True,
    )
    await agent_b._process_message(_request("pytest tests", "session-b"))
    assert "This repository uses pytest tests under tests." in "\n".join(
        str(message.get("content")) for message in provider_b.seen_messages
    )
    await agent_b.close()

    repo_b = tmp_path / "repo-b"
    repo_b.mkdir()
    provider_other = _CapturingProvider()
    agent_other = AgentLoop(
        provider=provider_other,
        workspace=repo_b,
        state=repo_b,
        model="stub",
        backend=None,
        memory_config=MemoryConfig(backend=None, user_id="alice", project_id="project-b"),
        max_iterations=1,
        restrict_to_workspace=True,
    )
    await agent_other._process_message(_request("pytest tests", "session-c"))
    assert "This repository uses pytest tests under tests." not in "\n".join(
        str(message.get("content")) for message in provider_other.seen_messages
    )
    await agent_other.close()


@pytest.mark.asyncio
async def test_I_corrupt_memory_does_not_corrupt_session_or_block_agentloop(tmp_path: Path) -> None:
    workspace = tmp_path / "corrupt-repo"
    workspace.mkdir()
    store = _store(workspace, repo="corrupt-repo")
    store.memory_items_file.write_text('{"schema":"invalid"}\n', encoding="utf-8")
    provider = _CapturingProvider()
    agent = AgentLoop(
        provider=provider,
        workspace=workspace,
        state=workspace,
        model="stub",
        backend=None,
        memory_config=MemoryConfig(backend=None, user_id="alice", project_id="corrupt-project"),
        max_iterations=1,
        restrict_to_workspace=True,
    )

    await agent._process_message(_request("continue despite corrupt memory", "corrupt-session"))

    persisted = agent.sessions.peek("test:corrupt-session")
    assert persisted is not None
    assert provider.seen_messages
    assert any(message.get("role") == "assistant" for message in persisted.messages)
    assert agent.effect_journal.load() == []
    await agent.close()


def test_I_unavailable_local_memory_storage_fails_closed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    blocked = tmp_path / "structured-directory"
    blocked.mkdir()
    store.structured_file = blocked

    assert store.recall_memory("anything") == []
    assert store.last_memory_diagnostics["warnings"] == ["storage_unavailable"]
    result = store.write(_candidate(store, "pytest is unavailable here", normalized_key="blocked"))
    assert result.accepted is False
    assert result.action == "error"
    assert result.reason == "storage_unavailable"


def test_null_backend_factory_keeps_empty_fast_path_but_accepts_local_items(tmp_path: Path) -> None:
    from looprail.agent.context import ContextBuilder

    builder = ContextBuilder(tmp_path, start_watcher=False)
    engine = build_context_engine(
        workspace=tmp_path,
        config=ContextConfig(),
        builder=builder,
        provider=_CapturingProvider(),
        model="stub",
        context_window_tokens=100_000,
        get_tool_definitions=lambda: [],
        backend=None,
        memory_config=MemoryConfig(backend=None, project_id="project-a"),
    )
    memory_builder = next(item for item in engine._builders if isinstance(item, MemorySegmentBuilder))
    assert memory_builder._enabled is False

    result = builder.memory.write(_candidate(builder.memory, "pytest is local", normalized_key="local"))
    assert result.accepted
    assert memory_builder._allow_local_structured is True


def test_optional_backend_unavailable_fails_closed(tmp_path: Path) -> None:
    config = LooprailConfig(memory=MemoryConfig(backend="missing-optional-memory"))

    with pytest.raises(PluginNotFoundError):
        maybe_build_memory_backend(tmp_path, config, registry=PluginRegistry())


def test_J_actual_looprail_run_reaches_structured_memory_segment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Enter through the same Typer command used by the approved ``looprail run`` surface."""

    from looprail.config.loader import save_config, set_config_path

    workspace = tmp_path / "cli-repo"
    workspace.mkdir()
    store = MemoryStore(workspace, repository_id=repository_identity_for_path(workspace))
    accepted = store.write(
        Memory(
            text="This repository uses pytest under tests.",
            kind=MemoryKind.REPO_CONVENTION.value,
            scope=MemoryScope.REPO_LOCAL.value,
            verification=MemoryVerification.VERIFIED.value,
            normalized_key="test layout",
            provenance={
                "source": "read-config",
                "source_type": "repository_observation",
                "repo_identity": store.repository_identity,
            },
        )
    )
    assert accepted.accepted
    provider = _CapturingProvider()
    monkeypatch.setattr("looprail.cli.agent_commands.make_provider", lambda _config: provider)
    config_path = tmp_path / "cli-config.json"
    set_config_path(config_path)
    try:
        save_config(Config())
        raw_config = json.loads(config_path.read_text(encoding="utf-8"))
        raw_config["memory"] = {"backend": None}
        config_path.write_text(json.dumps(raw_config), encoding="utf-8")
        result = CliRunner().invoke(
            app,
            [
                "run",
                "-m",
                "pytest tests",
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
    assert "This repository uses pytest under tests." in "\n".join(
        str(message.get("content")) for message in provider.seen_messages
    )
