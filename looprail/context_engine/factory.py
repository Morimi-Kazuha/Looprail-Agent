"""从共享依赖构建唯一 ContextAssembler 及其扁平 SegmentBuilder 列表。

旧 ``legacy`` / ``curator`` / ``default`` Engine 分派已经移除。当前每轮由一套 Assembler 协调：
Curator lane 建 manifest 并走 fast/slow/fallback History 选择，写入 ``# Curator Working State``
且独占 ``*history``；Memory lane 调用 ``backend.recall(user_id=...)`` 形成 segment 3
``# Memory``；Local Skill lane 用 :class:`SkillForgeRouter` 形成 segment 5 ``# Skills``；Host
通过 :class:`ContextBuilder` 提供 identity、bootstrap 与 always-skills。

SkillForgeRouter 包装 Builder 已有 ``LocalPool`` 与 ``SkillRegistry``，不会再扫一次磁盘。
    外部 Memory Backend 是否启用只影响 Plugin recall/store；显式存在的本地 Structured Memory 仍可
    通过同一个 Memory Segment 读取，不改变 Local Skill 可用性。Factory 的责任是接线和
默认配置，不执行某一 Turn 的选择；最终总是返回同一个 :class:`ContextAssembler` 类型。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Protocol

from looprail.agent.context import ContextBuilder
from looprail.context_engine.assembler import ContextAssembler
from looprail.context_engine.base import ContextEngine
from looprail.context_engine.segments import (
    ActiveSkillsSegmentBuilder,
    BootstrapSegmentBuilder,
    IdentitySegmentBuilder,
    MemorySegmentBuilder,
    SkillsSegmentBuilder,
)
from looprail.context_engine.segments.curator import CuratorSegmentBuilder
from looprail.providers.base import LLMProvider

if TYPE_CHECKING:
    from looprail.config.looprail import (
        ContextConfig,
        MemoryConfig,
        SkillForgeConfig,
        SkillForgeRouterConfig,
    )
    from looprail.memory_engine.backend import MemoryBackend
    from looprail.memory_engine.skill_forge import SkillForgeRouter


class ContextEngineFactory(Protocol):
    def __call__(
        self,
        *,
        workspace: Path,
        config: "ContextConfig",
        builder: ContextBuilder,
        provider: LLMProvider,
        model: str,
        context_window_tokens: int,
        get_tool_definitions: Callable[[], list[dict]],
        now_fn: Callable[[], datetime] | None = None,
        backend: "MemoryBackend | None" = None,
        memory_config: "MemoryConfig | None" = None,
        skill_forge_router_config: "SkillForgeRouterConfig | None" = None,
        skill_forge_config: "SkillForgeConfig | None" = None,
    ) -> ContextEngine: ...


def build_context_engine(
    *,
    workspace: Path,
    config: "ContextConfig",
    builder: ContextBuilder,
    provider: LLMProvider,
    model: str,
    context_window_tokens: int,
    get_tool_definitions: Callable[[], list[dict]],
    now_fn: Callable[[], datetime] | None = None,
    backend: "MemoryBackend | None" = None,
    memory_config: "MemoryConfig | None" = None,
    skill_forge_router_config: "SkillForgeRouterConfig | None" = None,
    skill_forge_config: "SkillForgeConfig | None" = None,
) -> ContextEngine:
    """从扁平 SegmentBuilder 列表构建唯一 :class:`ContextAssembler`。

    ``config.engine`` 已不再是 dispatch key，因为只有一个 Engine；字段留在
    :class:`ContextConfig` 仅为配置 back-compat，本函数有意忽略。``builder`` 当前只作为共享
    ``MemoryStore`` / ``LocalSkillCatalog`` 的持有者，待低层兼容结构退休后可移除。

    缺失 Memory 与 SkillForgeRouter 配置时创建默认值。Memory Segment 仅在 Backend 存在时
    enabled；Skill injection_mode 为 summary 时 ``activation_max=0``，否则采用 inject_max 或
    router top_k。Builder 按 identity、bootstrap、memory、active skills、router skills、
    Curator 顺序交给 Assembler，Tool definitions 使用延迟 callable 保持注册表为当前值。
    """
    from looprail.config.looprail import (
        MemoryConfig as _MemoryConfig,
    )
    from looprail.config.looprail import (
        SkillForgeRouterConfig as _SkillForgeRouterConfig,
    )

    if memory_config is None:
        memory_config = _MemoryConfig()
    if skill_forge_router_config is None:
        skill_forge_router_config = _SkillForgeRouterConfig()

    # The AgentLoop normally constructs ContextBuilder with these same
    # values.  Keep the public factory coherent for callers that supply an
    # already-created builder: its MemoryStore is still the one authority.
    if getattr(memory_config, "project_id", None) is not None:
        builder.memory.project_id = memory_config.project_id
    if hasattr(memory_config, "user_id"):
        builder.memory.user_id = memory_config.user_id

    router = _build_router(
        builder=builder,
        skill_forge_router_config=skill_forge_router_config,
    )
    configured_inject_max = int(getattr(skill_forge_config, "inject_max", 2)) if skill_forge_config is not None else 2
    summary_only = (
        skill_forge_config is not None and getattr(skill_forge_config, "injection_mode", "full_body") == "summary"
    )
    activation_max = 0 if summary_only else configured_inject_max or skill_forge_router_config.top_k

    # A null external backend keeps the historical no-op behavior when the
    # local structured store is empty, but an explicitly written local item
    # remains usable without installing Myna.  This preserves the old
    # no-backend fast path while making the Phase 5 deterministic core real.
    local_structured_memory = bool(getattr(builder.memory, "memory_items_file", None)) and bool(
        builder.memory.memory_items_file.exists()
    )

    builders = [
        IdentitySegmentBuilder(workspace, builder.state),
        BootstrapSegmentBuilder(builder.state),
        MemorySegmentBuilder(
            builder.memory,
            backend,
            user_id=memory_config.user_id,
            memory_top_k=memory_config.memory_top_k,
            enabled=backend is not None or local_structured_memory,
            project_id=getattr(memory_config, "project_id", None),
            structured_top_k=getattr(memory_config, "structured_top_k", 5),
            structured_max_chars=getattr(memory_config, "structured_max_chars", 6_000),
            allow_local_structured=True,
        ),
        ActiveSkillsSegmentBuilder(builder.skills),
        SkillsSegmentBuilder(
            router,
            skill_top_k=skill_forge_router_config.top_k,
            activation_max=activation_max,
        ),
        CuratorSegmentBuilder(
            workspace=builder.state,
            config=config,
            provider=provider,
            model=model,
            context_window_tokens=context_window_tokens,
            get_tool_definitions=get_tool_definitions,
            now_fn=now_fn,
            memory_enabled=backend is not None,
            memory_store=builder.memory,
        ),
    ]
    return ContextAssembler(
        builders,
        get_tool_definitions,
        now_fn=now_fn,
        provider=provider,
        model=model,
    )


def _build_router(
    *,
    builder: ContextBuilder,
    skill_forge_router_config: "SkillForgeRouterConfig",
) -> "SkillForgeRouter":
    """为 segment 5 组装只包含 operator-managed Local Skill 的路由器。

    `LocalSkillSource` 直接复用 ``builder.skills.pool`` 与 ``builder.skills.registry``，并应用
    ``local_min_score``；这避免重新扫描磁盘或建立第二份 Registry。返回的 `SkillForgeRouter`
    当前只有该 Source，top-k 与 activation 数量由上层 `SkillsSegmentBuilder` 控制，本函数不
    执行检索。
    """
    from looprail.memory_engine.skill_forge import (
        LocalSkillSource,
        SkillForgeRouter,
    )

    local_source = LocalSkillSource(
        pool=builder.skills.pool,
        registry=builder.skills.registry,
        min_score=skill_forge_router_config.local_min_score,
    )
    return SkillForgeRouter(sources=[local_source])
