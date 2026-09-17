"""实现唯一 Context Engine，把统一 SegmentBuilder 列表组装成本轮模型消息。

Phase A 并行运行所有 ``needs_prefix=False`` 的 Builder，即 seg1–5：identity、bootstrap、
memory、active-skills、skills。各自 ``text`` 按 order 连接成 System prefix，``meta`` 合并为
组装证据。Phase B 再运行 ``needs_prefix=True`` 的 Curator；此时 ``ctx.prefix`` 已含精确的
System prefix、User message 与 Tool definitions，所以它能用固定开销预算 ``*history``，并
贡献 segment 6 与唯一 History slot。

User message 是结构内建项，每个 Turn 恰好一个，不是可插拔 Builder；Tool 走 side channel，
随 messages 一起交给 LLM 并计入预算，但永不渲染成 Segment。`ContextAssembler` 最终只产出
``[system, *history, user]`` 和 metadata，不执行 Memory/Skill 的业务选择，也不调用主 Agent。
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable

from looprail.context_engine.base import (
    AssembledPrefix,
    AssemblyContext,
    ContextEngine,
    SegmentBuilder,
)
from looprail.context_engine.budget import ContextBudgetError, ContextDecision
from looprail.context_engine.history_trimmer import HistoryTrimmer
from looprail.context_engine.segments import render
from looprail.memory_engine.base import AssembledContext, TokenBudget
from looprail.tracing import trace
from looprail.utils.helpers import estimate_prompt_tokens, estimate_prompt_tokens_chain

if TYPE_CHECKING:
    from looprail.context_engine.curator import TurnContext


class ContextAssembler(ContextEngine):
    """把 SegmentBuilders 的两阶段产物合并为单 Turn Context 的唯一 Engine。

    构造时按 ``order`` 排序，并依据 ``needs_prefix`` 固定 Phase A/Phase B；`assemble` 每轮并行
    运行独立贡献者、建立 `AssembledPrefix`、再运行依赖固定开销的贡献者。实例长期由
    AgentLoop 持有，可通过 `replace_model` 把模型变化转发给需要它的 Builder。

    Engine ``owns_compaction=True``，因为 Curator 自行选择和归档 History；Host 必须传完整
    append-only 候选并跳过 MemoryConsolidator。最终 metadata 会带 ``engine`` 名称，便于 Turn
    evidence 确认实际组装路径。
    """

    def __init__(
        self,
        builders: list[SegmentBuilder],
        get_tool_definitions: Callable[[], list[dict[str, Any]]],
        now_fn: Callable[[], datetime] | None = None,
        provider: Any | None = None,
        model: str | None = None,
    ) -> None:
        self._builders = sorted(builders, key=lambda b: b.order)
        self._phase_a = [b for b in self._builders if not b.needs_prefix]
        self._phase_b = [b for b in self._builders if b.needs_prefix]
        self.get_tool_definitions = get_tool_definitions
        self._now_fn = now_fn or datetime.now
        self.provider = provider
        self.model = model
        # The first local tokenizer lookup can take a few hundred milliseconds
        # while cl100k_base is loaded.  Warm it during engine construction so
        # the Phase-A concurrency contract measures the two independent lanes,
        # not one-time tokenizer initialization after they finish.
        try:
            estimate_prompt_tokens([{"role": "system", "content": "warm"}])
        except Exception:  # noqa: BLE001
            pass

    @property
    def name(self) -> str:
        return "context_assembler"

    @property
    def owns_compaction(self) -> bool:
        # Curator 路径自行归档历史，因此 AgentLoop 向其传入完整的追加式日志，
        # 并跳过 Host 的 MemoryConsolidator。
        return True

    def replace_model(self, model: str) -> None:
        self.model = model
        for builder in self._builders:
            replace_model = getattr(builder, "replace_model", None)
            if callable(replace_model):
                replace_model(model)

    async def assemble(
        self,
        session_key: str,
        session_messages: list[dict[str, Any]],
        budget: TokenBudget,
        *,
        turn: "TurnContext",
    ) -> AssembledContext:
        ctx = AssemblyContext(
            session_key=session_key,
            current_message=turn.current_message,
            media=turn.media,
            channel=turn.channel,
            chat_id=turn.chat_id,
            session_messages=session_messages,
            budget=budget,
            recovery_evidence=turn.recovery_evidence,
        )

        # ── 阶段 A——相互独立的片段构建器，并发执行 ──────
        a_segs = await asyncio.gather(*[b.build(ctx) for b in self._phase_a])
        meta: dict[str, Any] = {}
        prefix_parts: list[str] = []
        for seg in a_segs:
            if seg is None:
                continue
            meta |= seg.meta
            if seg.text:
                prefix_parts.append(seg.text)
        system_prefix = "\n\n---\n\n".join(prefix_parts)

        user_msg = self._build_user(ctx)

        # ── 阶段 B——依赖前缀的构建器（Curator），串行执行 ───
        ctx_b = replace(
            ctx,
            prefix=AssembledPrefix(
                system_prefix=system_prefix,
                user_message=user_msg,
                tool_defs=self.get_tool_definitions(),
            ),
        )
        b_segs = await asyncio.gather(*[b.build(ctx_b) for b in self._phase_b])

        system = system_prefix
        history: list[dict[str, Any]] = []
        seg6_parts: list[str] = []
        for seg in b_segs:
            if seg is None:
                continue
            meta |= seg.meta
            if seg.text:
                seg6_parts.append(seg.text)
            if seg.history is not None:
                history = seg.history
        for text in seg6_parts:
            system = system + "\n\n---\n\n" + text

        messages = [{"role": "system", "content": system}, *history, user_msg]
        tool_defs = self.get_tool_definitions()
        estimated_before, source_before = self._estimate(
            [
                {"role": "system", "content": system},
                *HistoryTrimmer.history_from_ids(
                    session_messages,
                    HistoryTrimmer.canonical_ids(session_messages, list(range(len(session_messages)))),
                ),
                user_msg,
            ],
            tool_defs,
        )
        estimated_after, source_after = self._estimate(messages, tool_defs)
        structural_errors = HistoryTrimmer.structural_errors(messages)
        history_decisions = self._history_decisions(meta)
        fixed_decisions = self._fixed_decisions(system, user_msg, tool_defs, turn.recovery_evidence)
        decisions = fixed_decisions + history_decisions
        validation = meta.get("validation")
        if isinstance(validation, dict):
            validation.setdefault("estimated_tokens_before", estimated_before)
            validation.setdefault("estimated_tokens_after", estimated_after)
            validation.setdefault("estimate_source_before", source_before)
            validation.setdefault("estimate_source_after", source_after)
            validation.setdefault("input_context_budget", budget.input_context_budget)
            validation.setdefault("runtime_margin", budget.runtime_margin)
            validation.setdefault("decisions", history_decisions)

        final_metadata = meta | {
            "engine": self.name,
            "context_budget": {
                "context_limit": budget.context_length,
                "provider_context_limit": budget.provider_context_limit,
                "input_context_budget": budget.input_context_budget,
                "reserved_output": budget.reserved_output,
                "runtime_margin": budget.runtime_margin,
                "source": budget.budget_source,
            },
            "context_estimate": {
                "before": estimated_before,
                "after": estimated_after,
                "source_before": source_before,
                "source_after": source_after,
            },
            "context_decisions": decisions[:256],
            "context_structural_errors": structural_errors,
        }

        trace_attrs = {
            "context.limit": budget.context_length,
            "context.provider_limit": budget.provider_context_limit,
            "context.input_budget": budget.input_context_budget,
            "context.estimated_tokens_before": estimated_before,
            "context.estimated_tokens_after": estimated_after,
            "context.reserved_output": budget.reserved_output,
            "context.runtime_margin": budget.runtime_margin,
            "context.items_kept": sum(1 for item in decisions if item.get("decision") == "KEEP"),
            "context.items_compacted": sum(
                1 for item in decisions if item.get("decision") in {"COMPACT", "SUMMARIZE"}
            ),
            "context.items_dropped": sum(1 for item in decisions if item.get("decision") == "DROP"),
            "context.summary_used": any(item.get("decision") == "SUMMARIZE" for item in decisions),
            "context.reason": "assembled",
            "context.estimate_source": source_after,
        }
        with trace.span("context.assemble", trace_attrs, kind="memory", session_key=session_key) as span:
            span.artifact("context.decisions", decisions[:256])
            if structural_errors:
                span.error("tool_pair_integrity")
                raise ContextBudgetError(
                    estimated_tokens=estimated_after,
                    input_context_budget=budget.input_context_budget,
                    context_limit=budget.context_length,
                    reserved_output=budget.reserved_output,
                    runtime_margin=budget.runtime_margin,
                    reason="tool_pair_integrity",
                )
            if estimated_after > budget.input_context_budget:
                fixed_tokens, _ = self._estimate(
                    [{"role": "system", "content": system}, user_msg],
                    tool_defs,
                )
                reason = (
                    "protected_fixed_context_exceeds_budget"
                    if fixed_tokens > budget.input_context_budget
                    else "context_assembly_overflow"
                )
                span.set({"context.reason": reason})
                span.error(reason)
                raise ContextBudgetError(
                    estimated_tokens=estimated_after,
                    input_context_budget=budget.input_context_budget,
                    context_limit=budget.context_length,
                    reserved_output=budget.reserved_output,
                    runtime_margin=budget.runtime_margin,
                    reason=reason,
                    protected_tokens=fixed_tokens,
                )

        include_indices = meta.get("included_message_ids")
        if not isinstance(include_indices, list):
            include_indices = None
        return AssembledContext(
            messages=messages,
            include_indices=include_indices,
            metadata=final_metadata,
        )

    def _estimate(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> tuple[int, str]:
        if self.provider is not None:
            return estimate_prompt_tokens_chain(self.provider, self.model, messages, tools)
        return estimate_prompt_tokens(messages, tools), "tiktoken"

    @staticmethod
    def _history_decisions(meta: dict[str, Any]) -> list[dict[str, Any]]:
        validation = meta.get("validation")
        if not isinstance(validation, dict):
            return []
        decisions = validation.get("decisions")
        if not isinstance(decisions, list):
            return []
        return [item for item in decisions if isinstance(item, dict)]

    @staticmethod
    def _fixed_decisions(
        system: str,
        user_msg: dict[str, Any],
        tool_defs: list[dict[str, Any]],
        recovery_evidence: str | None,
    ) -> list[dict[str, Any]]:
        out = [
            ContextDecision(
                source="system_prefix",
                layer="L0/L1/L3",
                estimated_tokens=estimate_prompt_tokens([{"role": "system", "content": system}]),
                priority=1.0,
                decision="KEEP",
                reason="runtime and project contract are fixed inputs",
                protected=True,
            ).to_dict(),
            ContextDecision(
                source="tool_definitions",
                layer="L0",
                estimated_tokens=estimate_prompt_tokens([], tool_defs),
                priority=1.0,
                decision="KEEP",
                reason="provider tool schema is required for valid calls",
                protected=True,
            ).to_dict(),
            ContextDecision(
                source="current_user_request",
                layer="L2",
                estimated_tokens=estimate_prompt_tokens([user_msg]),
                priority=1.0,
                decision="KEEP",
                reason="current task is never trimmed as ordinary history",
                protected=True,
            ).to_dict(),
        ]
        if recovery_evidence:
            out.append(
                ContextDecision(
                    source="recovery_evidence",
                    layer="L2",
                    estimated_tokens=estimate_prompt_tokens(
                        [{"role": "user", "content": recovery_evidence}]
                    ),
                    priority=1.0,
                    decision="KEEP",
                    reason="active recovery warning is protected in the current Turn",
                    protected=True,
                ).to_dict()
            )
        return out

    async def after_turn(
        self,
        session_key: str,
        outcome: dict[str, Any],
        usage: dict[str, int] | None = None,
    ) -> None:
        # 委托给需要维护每 Turn 账目的 builder（例如 Curator）。
        for builder in self._builders:
            hook = getattr(builder, "after_turn", None)
            if hook is not None:
                await hook(session_key, outcome, usage)

    def _build_user(self, ctx: AssemblyContext) -> dict[str, Any]:
        """构造唯一结构化 User message，把运行时上下文放在真实内容之前。

        `render.build_runtime_context` 根据当前时间、Channel 与 Chat 生成每轮环境前缀，
        `render.build_user_content` 则把 ``current_message`` 和 Media 转成 Provider 可接受内容。
        纯文本用两个换行连接；多模态列表在首位插入 runtime text block，保持图片等后续块顺序。

        返回固定 ``{"role": "user", "content": merged}`` 形状。该运行时前缀只供本轮模型使用，
        Session 持久化会把它剥离，避免每轮动态时间污染长期用户历史。
        """
        runtime_ctx = render.build_runtime_context(self._now_fn, ctx.channel, ctx.chat_id)
        user_content = render.build_user_content(ctx.current_message, ctx.media)
        recovery = (ctx.recovery_evidence or "").strip()
        if isinstance(user_content, str):
            prefix = f"{recovery}\n\n" if recovery else ""
            merged: Any = f"{prefix}{runtime_ctx}\n\n{user_content}"
        else:
            prefix_blocks = [{"type": "text", "text": recovery}] if recovery else []
            merged = prefix_blocks + [{"type": "text", "text": runtime_ctx}] + user_content
        return {"role": "user", "content": merged}


__all__ = ["ContextAssembler"]
