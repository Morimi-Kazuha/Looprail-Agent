"""`MemoryBackend` Protocol，是每个 Memory Plugin 都要实现的 Single Contract。

MB-1 引入这条 **New Seam**，连接 `AgentLoop` 与 Memory Subsystem。它刻意区别于
:mod:`pico.memory_engine.base` 中旧的 :class:`MemoryEngine` ABC，使代码库迁移期间两套接口可以共存。

Plugin Authors 必须理解三个 Design Points：

- ``recall`` 显式命名 Track，接收 ``user_id`` XOR ``agent_id``。Active Host 的 Memory 使用 User Track；
  Agent Track 为 Public Plugin Protocol Compatibility 保留。
- ``Memory.metadata`` 是 **Escape Hatch**。``text`` 与 ``score`` 已标准化；Categories、Episode Type、
  Native ID、Source Labels 等 Backend-specific 信息全部放入 ``metadata``。Host Context Assembler **不**
  读取 Metadata，只有 Pre-rendered ``text`` 进入 Prompt；把 Memory Hit 重新发为 `ScoredSkill` 的
  Skill-source Adapter 才读取 Metadata 构造 Qualified ID。
- ``feedback`` **允许 No-op**。它为 Public Protocol Compatibility 保留，但 Active Host 当前不 Dispatch。

Protocol 使用 :func:`typing.runtime_checkable`，所以 Tests 可执行 ``isinstance(x, MemoryBackend)``；代价
是任何 Surface Matching 的 Class，包括 Duck-typed Mocks，都会通过。Contract Tests 因此无需继承 Base
Class，但 Runtime Check 也不证明语义实现正确。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Structured item vocabulary
# ---------------------------------------------------------------------------


class MemoryScope(StrEnum):
    """The explicit visibility/durability boundary of a Memory item."""

    TURN_LOCAL = "turn_local"
    SESSION_LOCAL = "session_local"
    PROJECT = "project"
    REPO_LOCAL = "repo_local"
    GLOBAL = "global"
    USER = "user"


class MemoryKind(StrEnum):
    """The small, explainable set of reusable fact classes supported in Phase 5."""

    PROJECT_FACT = "project_fact"
    USER_PREFERENCE = "user_preference"
    REPO_CONVENTION = "repo_convention"
    SUCCESSFUL_PROCEDURE = "successful_procedure"
    FAILURE_LESSON = "failure_lesson"
    ENVIRONMENT_FACT = "environment_fact"
    UNCLASSIFIED = "unclassified"


class MemoryVerification(StrEnum):
    """How an item got its evidence; model output is never implicitly verified."""

    OBSERVED = "observed"
    VERIFIED = "verified"
    DERIVED = "derived"
    USER_PROVIDED = "user_provided"
    UNCERTAIN = "uncertain"


class MemoryStatus(StrEnum):
    """Lifecycle state of a stored item."""

    ACTIVE = "active"
    SUPERSEDED = "superseded"
    INVALIDATED = "invalidated"
    TOMBSTONED = "tombstoned"
    STALE = "stale"


MEMORY_ITEM_SCHEMA = "pico.memory.item.v1"
MEMORY_SCOPES = frozenset(item.value for item in MemoryScope)
MEMORY_KINDS = frozenset(item.value for item in MemoryKind)
MEMORY_VERIFICATIONS = frozenset(item.value for item in MemoryVerification)
MEMORY_STATUSES = frozenset(item.value for item in MemoryStatus)

_MEMORY_NORMALIZE_RE = re.compile(r"[^\w\s:/._-]+", re.UNICODE)
_PROVENANCE_KEYS = frozenset(
    {
        "source",
        "source_type",
        "session_id",
        "turn_id",
        "tool_name",
        "result_ref",
        "repo_identity",
        "project_id",
        "user_id",
        "observed_at",
        "source_path",
        "source_digest",
        "evidence_digest",
    }
)
_PROVENANCE_MAX_LENGTHS = {
    "source": 128,
    "source_type": 64,
    "session_id": 256,
    "turn_id": 256,
    "tool_name": 128,
    "result_ref": 512,
    "repo_identity": 512,
    "project_id": 256,
    "user_id": 256,
    "observed_at": 64,
    "source_path": 1024,
    "source_digest": 128,
    "evidence_digest": 128,
}


def normalize_memory_text(value: Any) -> str:
    """Return the deterministic lexical form used for item identity and ranking."""

    text = "" if value is None else str(value)
    text = _MEMORY_NORMALIZE_RE.sub(" ", text.casefold())
    return " ".join(text.split())


def memory_content_digest(text: str) -> str:
    """Hash only the normalized claim text; raw tool payloads are never needed."""

    return hashlib.sha256(normalize_memory_text(text).encode("utf-8")).hexdigest()


def _bounded_value(key: str, value: Any) -> str | None:
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[: _PROVENANCE_MAX_LENGTHS.get(key, 256)]


def normalize_memory_provenance(value: Any) -> dict[str, str]:
    """Keep only bounded provenance references, excluding arbitrary payloads/logs."""

    if isinstance(value, MemoryProvenance):
        value = value.to_dict()
    if not isinstance(value, Mapping):
        return {}
    out: dict[str, str] = {}
    for key in sorted(_PROVENANCE_KEYS):
        bounded = _bounded_value(key, value.get(key))
        if bounded is not None:
            out[key] = bounded
    return out


@dataclass(frozen=True)
class MemoryProvenance:
    """Bounded source evidence carried with a durable Memory item.

    References and digests are intentionally scalar and bounded.  The actual
    ToolResult, Session transcript, reasoning trace, or recovery payload does
    not belong here.
    """

    source: str = "runtime"
    source_type: str = "manual"
    session_id: str | None = None
    turn_id: str | None = None
    tool_name: str | None = None
    result_ref: str | None = None
    repo_identity: str | None = None
    project_id: str | None = None
    user_id: str | None = None
    observed_at: str | None = None
    source_path: str | None = None
    source_digest: str | None = None
    evidence_digest: str | None = None

    def to_dict(self) -> dict[str, str]:
        return normalize_memory_provenance(
            {
                "source": self.source,
                "source_type": self.source_type,
                "session_id": self.session_id,
                "turn_id": self.turn_id,
                "tool_name": self.tool_name,
                "result_ref": self.result_ref,
                "repo_identity": self.repo_identity,
                "project_id": self.project_id,
                "user_id": self.user_id,
                "observed_at": self.observed_at,
                "source_path": self.source_path,
                "source_digest": self.source_digest,
                "evidence_digest": self.evidence_digest,
            }
        )

# ---------------------------------------------------------------------------
# 数据载体
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Memory:
    """一次 :meth:`MemoryBackend.recall` 返回的 Hit，也可作为结构化 item。

    ``frozen=True`` 让 Host 可以在组件间传递 Memory List，而无需担心 Adapter Code 重新绑定字段、修改
    他人视图。`text` 是可注入内容，`score` 是标准化相关度，`metadata` 保留 Backend Details；Frozen
    不会深度冻结 Metadata Dict，Consumer 仍应把它当作只读。

    The fields after ``metadata`` are deliberately defaulted so existing
    plugin adapters remain source-compatible.  A bare plugin hit is
    ``unclassified``/``turn_local``/``uncertain`` and therefore cannot be
    accepted by the durable structured writer until an explicit policy
    decision supplies the missing evidence.
    """

    text: str
    """LLM 在 Prompt 中 Verbatim 看到的 Pre-rendered Content。

    Adapter 负责 Formatting，例如 EverMem 返回 Natural-sentence Facts，Mem0 返回 Category-tagged Blobs。
    Host 除了把多个 Hits Join 成 Block 外，**永不** Post-process ``text``；因此去敏、边界标记与可读性
    必须在进入该字段前完成。
    """

    score: float = 0.0
    """由 Adapter 归一化到 ``[0, 1]`` 的 Relevance。

    Memory Hit 被重新发为 `ScoredSkill` 时，:class:`SkillForgeRouter` 用它做 Cross-source RRF。普通
    ``# Recalled memory`` Injection 中该值仅 Informational，不决定文本是否进入 Prompt。
    """

    metadata: dict[str, Any] = field(default_factory=dict)
    """Adapter-specific Escape Hatch。各 Backend Examples：

    - EverMem: ``{"id": ..., "episode_type": ..., "name": ...,
      "owner_type": "user" | "agent"}``
    - mem0: ``{"id": ..., "categories": [...], "memory_type": ...}``
    - MemOS: ``{"id": ..., "mem_cube_id": ..., "metadata": {...}}``
    - Letta: ``{"archival_memory_id": ...}``
    Host Context Injection 不读取这些字段；它们主要用于 Provenance、Qualified ID 与 Backend-native
    Correlation，不能代替 `text` 中面向模型的内容。
    """

    # Structured Memory lifecycle fields.  They are not required for the
    # legacy opaque backend contract above.
    kind: str = MemoryKind.UNCLASSIFIED.value
    scope: str = MemoryScope.TURN_LOCAL.value
    verification: str = MemoryVerification.UNCERTAIN.value
    confidence: str = "unknown"
    status: str = MemoryStatus.ACTIVE.value
    memory_id: str | None = None
    normalized_key: str | None = None
    content_digest: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)
    created_at: str | None = None
    updated_at: str | None = None
    version: int = 1
    supersedes: str | None = None
    superseded_by: str | None = None
    invalidated_reason: str | None = None

    @property
    def id(self) -> str | None:
        """Compatibility identity view used by adapters and diagnostics."""

        return self.memory_id or (str(self.metadata.get("id")) if self.metadata.get("id") else None)

    def structured_dict(self) -> dict[str, Any]:
        """Return the JSON-compatible item shape used by :class:`MemoryStore`."""

        return {
            "schema": MEMORY_ITEM_SCHEMA,
            "text": self.text,
            "kind": self.kind,
            "scope": self.scope,
            "verification": self.verification,
            "confidence": self.confidence,
            "status": self.status,
            "metadata": self.metadata,
            "memory_id": self.memory_id,
            "normalized_key": self.normalized_key,
            "content_digest": self.content_digest,
            "provenance": normalize_memory_provenance(self.provenance),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "version": self.version,
            "supersedes": self.supersedes,
            "superseded_by": self.superseded_by,
            "invalidated_reason": self.invalidated_reason,
        }


# ``MemoryItem`` is a named view of the existing public ``Memory`` carrier,
# not a second memory model or store.  This lets Phase 5 callers express the
# durable intent without breaking adapters that already return ``Memory``.
MemoryItem = Memory


@dataclass(frozen=True)
class MemoryWriteResult:
    """Explainable outcome of one structured write attempt."""

    accepted: bool
    action: str
    reason: str
    item: Memory | None = None
    duplicate_of: str | None = None
    superseded_ids: tuple[str, ...] = ()
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "action": self.action,
            "reason": self.reason,
            "memory_id": self.item.memory_id if self.item else None,
            "duplicate_of": self.duplicate_of,
            "superseded_ids": list(self.superseded_ids),
            "diagnostics": dict(self.diagnostics),
        }


# ---------------------------------------------------------------------------
# 协议
# ---------------------------------------------------------------------------


@runtime_checkable
class MemoryBackend(Protocol):
    """所有 Memory Plugins 实现的 Single Runtime Contract。

    Five Methods 按 Hot-path 排列：

    1. :meth:`recall`：``ContextEngine.assemble`` 每 Turn 以 ``user_id`` 读取 User-track Memory；
    2. :meth:`store`：AgentLoop 每 Turn 后持久化 Conversation Slice；
    3. :meth:`feedback`：允许 No-op 的 Compatibility Hook；
    4. :meth:`start` / :meth:`stop`：由 Host Await 的 Lifecycle。

    Backend 拥有 Transport/Storage State，Host 拥有调用时机与 Context Assembly。协议方法返回不自动证明
    Store 已 Durable 或 Recalled Text 适合正向结论，具体 Adapter 必须提供这些保证。

    ``store(session_id, messages)`` is retained as the compatibility seam for
    existing plugins that ingest a conversation slice. It is not the Phase 5
    structured-item writer: the host's ``MemoryStore.write`` applies the
    explicit eligibility policy before a durable fact is accepted.
    """

    async def recall(
        self,
        query: str,
        *,
        user_id: str | None = None,
        agent_id: str | None = None,
        top_k: int,
    ) -> list[Memory]:
        """为一个 Track 检索匹配 ``query`` 的 Memories。

        ``user_id`` / ``agent_id`` 必须 Exactly One Set，即 XOR。Caller 在构造路径已经知道 Track，所以
        显式命名，而不是塞进带 Prefix 的 Opaque String。Backend 可以只支持 ``user_id``，对 Agent Track
        返回 ``[]``；Neither/Both 都是 Caller Bug，也应返回空列表。

        Empty Result 是合法的 No Hits；Transport Error、Auth Failure 等应 Raise。Host 会通过
        ``SkillForgeRouter._safe_search`` 降级到其他 Sources。`top_k` 是最大候选意图，不保证 Backend
        一定返回该数量，成功返回也不表示内容已进入最终 Prompt。
        """
        ...

    async def store(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
    ) -> None:
        """持久化一个 Session Slice。

        ``messages`` 使用 AgentLoop 已产生的 ``{"role", "content", ...}`` List-of-dicts Shape，Adapter
        无需中间转换。Backend 可自行 Chunk、Deduplicate 或 Extract；Protocol 对每次调用采用
        Fire-and-forget 结果形态，不返回对象。

        Transport/Auth Error 必须 Raise，让 AgentLoop Surface；Host **不会** Silently Swallow Store
        Failure。正常返回的 Durable 含义由 Backend 实现定义，若远端只接受异步队列，Adapter 应在自身
        文档中说明证据边界。
        """
        ...

    async def feedback(self, signals: dict[str, Any]) -> None:
        """消费 Free-form Signal Dict，例如 Injected/Used Skill IDs。

        No-op Implementation 完全 Valid 且 Idiomatic，因为 Active Host 当前不 Dispatch 该 Hook。未来使用
        时，各 Backend 必须自行验证 Signal Schema；调用不应被视为学习或演化已完成。
        """
        ...

    async def start(self) -> None:
        """执行 One-time / Idempotent Initialization，例如 Open Connections、Warm Caches、Run Migrations。

        Host 在 Agent Boot 时 Exactly Once Await；Failure 会 Abort Startup。实现仍应保持 Idempotent，便于
        部分初始化后的清理或防御性重试。
        """
        ...

    async def stop(self) -> None:
        """执行 One-time / Idempotent Teardown。

        Adapter 必须让它在 Failed ``start`` 后也能安全调用，以清理 Partial-init State。Stop 返回只说明
        Backend Lifecycle 已收尾，不影响已写入的 Durable Memory。
        """
        ...


@runtime_checkable
class StructuredMemoryBackend(Protocol):
    """Optional capability for plugins that natively understand Memory items.

    This is intentionally separate from :class:`MemoryBackend`: adding a
    required method to the legacy protocol would break existing adapters. A
    plugin may expose these methods, but the core runtime never requires them
    and remains usable with the local ``MemoryStore`` or a null backend.
    """

    async def write_memory(self, item: Memory) -> MemoryWriteResult | None: ...

    async def recall_memory(
        self,
        query: str,
        *,
        scope: str | None = None,
        repo_identity: str | None = None,
        project_id: str | None = None,
        user_id: str | None = None,
        top_k: int = 5,
    ) -> list[Memory]: ...


__all__ = [
    "MEMORY_ITEM_SCHEMA",
    "MEMORY_KINDS",
    "MEMORY_SCOPES",
    "MEMORY_STATUSES",
    "MEMORY_VERIFICATIONS",
    "Memory",
    "MemoryBackend",
    "MemoryItem",
    "MemoryKind",
    "MemoryProvenance",
    "MemoryScope",
    "MemoryStatus",
    "MemoryVerification",
    "MemoryWriteResult",
    "StructuredMemoryBackend",
    "memory_content_digest",
    "normalize_memory_provenance",
    "normalize_memory_text",
]
