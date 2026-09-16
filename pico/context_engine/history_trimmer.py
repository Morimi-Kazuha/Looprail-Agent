"""负责 Curator 对 ``*history`` 的唯一选择、结构闭包和预算裁剪路径。

Session 消息不能按单条任意删除：Assistant 的 ``tool_calls`` 与对应 Tool result 构成协议原子组，
缺一边都会让 Provider 看到 dangling call 或 orphan result。本模块从 :class:`CuratorAssembler`
抽出，让 Curator 与统一 Context Engine 共享同一实现：:meth:`canonical_ids` 补全相邻调用组，
:meth:`history_from_ids` 只保留 Provider-safe keys，:meth:`structural_errors` 验证双向配对，
:meth:`trim` 再按整 Turn 组删除最低优先级且未保护的历史直到预算允许。

这是选择 ``*history`` 的 *only* code path。Segment 6 ``# Curator Working State`` 由
:class:`ContextBuilder` 根据 plan 的 working-state text 渲染，不属于本模块；Trimmer 只通过
``build_messages`` 看到完整固定开销并决定 History，不拥有 System/User Prompt 组成。
最终包含哪些索引、为何删除某组消息以及是否仍然超限，都会作为 Outcome 返回供上层复核。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from pico.context_engine.budget import ContextBudgetError, ContextDecision
from pico.providers.base import LLMProvider
from pico.utils.helpers import estimate_message_tokens, estimate_prompt_tokens_chain

# Provider 安全的消息字段。会话消息上的其他字段
# （时间戳、内部 ID、清单标注）会在此前丢弃
# 必须在字典到达 LLM 前移除。reasoning_content / thinking_blocks 必须保留，
# 才能维持多 Turn 推理契约（如 DeepSeek thinking mode）；下游 Provider 门禁
# 会针对非 Anthropic 目标移除 thinking_blocks。
_ALLOWED_KEYS = {
    "role",
    "content",
    "tool_calls",
    "tool_call_id",
    "name",
    "reasoning_content",
    "thinking_blocks",
}


@dataclass
class TrimOutcome:
    """记录一次 :meth:`HistoryTrimmer.trim` 的选择结果与预算证据。

    ``history`` 是清理后的 Provider-safe 消息，``included_ids`` 对应原 Session 索引；
    ``estimated_tokens``、``max_prompt_tokens`` 与 ``source`` 说明估算值、允许上限和估算来源，
    ``warnings`` 记录为适配预算而删除的 Turn 组。`ok` 只判断 Token 是否落在上限内，`over_by`
    给出仍超出的非负数量；二者不证明语义选择正确。
    """

    history: list[dict[str, Any]]
    included_ids: list[int]
    estimated_tokens: int
    max_prompt_tokens: int
    source: str
    warnings: list[str] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.estimated_tokens <= self.max_prompt_tokens

    @property
    def over_by(self) -> int:
        return max(0, self.estimated_tokens - self.max_prompt_tokens)


class HistoryTrimmer:
    """把 Session 候选消息整形成结构合法且尽量符合预算的 ``*history``。

    实例持有 Provider、模型、延迟 Tool definitions 与 Context window，用统一 Token estimate
    评估完整 Prompt。纯整形方法不执行 I/O；`trim` 才反复调用传入的 `build_messages`，以真实
    System、User、Tools 固定开销为准删除历史。Protected Turn 不会被预算算法主动丢弃，因此
    若固定开销和保护内容本身超限，Outcome 会明确 ``ok=False`` 而不是破坏保护边界。
    """

    def __init__(
        self,
        provider: LLMProvider,
        model: str,
        get_tool_definitions: Callable[[], list[dict[str, Any]]],
        context_window_tokens: int,
        runtime_margin_tokens: int = 0,
    ) -> None:
        self.provider = provider
        self.model = model
        self.get_tool_definitions = get_tool_definitions
        self.context_window_tokens = context_window_tokens
        self.runtime_margin_tokens = max(0, int(runtime_margin_tokens))

    # ------------------------------------------------------------------
    # 纯历史整形辅助函数（不估算 token，也不执行 I/O）
    # ------------------------------------------------------------------

    @staticmethod
    def canonical_ids(messages: list[dict[str, Any]], ids: list[int]) -> list[int]:
        """对 ``ids`` 执行 Tool call/result adjacency closure，并规范起始边界。

        输入中的非法、越界索引先丢弃；选择 Assistant ``tool_calls`` 会自动带上相同 call id 的
        Tool results，选择任一 result 也会补回 Parent Assistant，直到集合稳定。结果按 Session
        原顺序返回，并裁掉第一个 ``role="user"`` 之前的项，确保 History 不从 Tool exchange
        中间开始。没有 User message 存活时返回 ``[]``，不伪造起点。
        """
        selected = {mid for mid in ids if isinstance(mid, int) and 0 <= mid < len(messages)}
        tool_parent_by_call: dict[str, int] = {}
        tool_result_by_call: dict[str, list[int]] = {}
        for idx, message in enumerate(messages):
            if message.get("role") == "assistant" and message.get("tool_calls"):
                for tc in message.get("tool_calls") or []:
                    if isinstance(tc, dict) and tc.get("id"):
                        tool_parent_by_call[str(tc["id"])] = idx
            if message.get("role") == "tool" and message.get("tool_call_id"):
                tool_result_by_call.setdefault(str(message["tool_call_id"]), []).append(idx)

        changed = True
        while changed:
            changed = False
            for call_id, parent_idx in tool_parent_by_call.items():
                result_ids = tool_result_by_call.get(call_id, [])
                if parent_idx in selected:
                    for rid in result_ids:
                        if rid not in selected:
                            selected.add(rid)
                            changed = True
                if any(rid in selected for rid in result_ids) and parent_idx not in selected:
                    selected.add(parent_idx)
                    changed = True

        ordered = sorted(selected)
        for pos, mid in enumerate(ordered):
            if messages[mid].get("role") == "user":
                return ordered[pos:]
        return []

    @staticmethod
    def history_from_ids(messages: list[dict[str, Any]], ids: list[int]) -> list[dict[str, Any]]:
        """把选中 Session 消息投影到 Provider-safe key 集合。

        对每个索引只保留 `_ALLOWED_KEYS` 中的 role、content、Tool 配对与受支持推理字段，移除
        timestamp、内部 ID 和 Manifest 标注；没有 role 的结果不进入 History。返回新字典列表，
        不修改 append-only Session。`thinking_blocks` 是否适合具体目标 Provider 由下游门禁再
        判断，本层必须先保留多 Turn reasoning contract。
        """
        history: list[dict[str, Any]] = []
        for mid in ids:
            clean = {k: v for k, v in messages[mid].items() if k in _ALLOWED_KEYS}
            if clean.get("role"):
                history.append(clean)
        return history

    @staticmethod
    def structural_errors(messages: list[dict[str, Any]]) -> list[str]:
        """验证已组装消息中的 Tool-call closure，并返回全部结构错误。

        Assistant 声明的每个 Tool call id 会进入 open set；Tool result 必须引用其中一个 id，
        否则报告 no parent assistant tool_call，配对成功则关闭该 id。遍历结束仍开放的调用会
        报 missing results。函数不修复顺序、不抛首个异常，调用方可据错误列表拒绝 Curator
        candidate 并允许重试。
        """
        errors: list[str] = []
        open_calls: set[str] = set()
        for msg in messages:
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                for tc in msg.get("tool_calls") or []:
                    if isinstance(tc, dict) and tc.get("id"):
                        open_calls.add(str(tc["id"]))
            if msg.get("role") == "tool":
                call_id = str(msg.get("tool_call_id", ""))
                if call_id not in open_calls:
                    errors.append(f"tool result {call_id} has no parent assistant tool_call")
                else:
                    open_calls.remove(call_id)
        if open_calls:
            errors.append(f"assistant tool_calls missing results: {sorted(open_calls)}")
        return errors

    @staticmethod
    def _turn_groups(messages: list[dict[str, Any]], ids: list[int]) -> list[list[int]]:
        groups: list[list[int]] = []
        current: list[int] = []
        for mid in ids:
            if messages[mid].get("role") == "user" and current:
                groups.append(current)
                current = []
            current.append(mid)
        if current:
            groups.append(current)
        return groups

    # ------------------------------------------------------------------
    # 预算驱动的裁剪
    # ------------------------------------------------------------------

    def trim(
        self,
        *,
        session_messages: list[dict[str, Any]],
        ids: list[int],
        protected_ids: set[int],
        reserved_output: int,
        build_messages: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
        priority_scores: dict[int, float] | None = None,
        context_window_tokens: int | None = None,
        runtime_margin_tokens: int | None = None,
    ) -> tuple[list[dict[str, Any]], TrimOutcome]:
        """闭包 ``ids``、构建完整 Prompt，并按整 Turn 组删除直至预算允许或无法再删。

        ``build_messages`` 把 History 映射为 system + history + user 完整列表；Caller 仍拥有
        segments、working state、router skills 等 Prompt composition，Trimmer 只拥有 History
        selection。每轮用 Provider/模型/Tool definitions 估算 Token，允许上限是 Context window
        减 ``reserved_output``，且至少为 1。

        超限时先按 User 边界分 Turn group，排除包含 ``protected_ids`` 的组，再按组内最高
        priority 和新旧顺序选择最低项删除；之后重新执行 canonical closure、构建和估算。
        没有可删组时保留超限事实。返回最终 ``messages`` 与 :class:`TrimOutcome`，warnings
        精确列出被删消息索引。
        """
        canon = self.canonical_ids(session_messages, ids)
        history = self.history_from_ids(session_messages, canon)
        messages = build_messages(history)

        estimated, source = estimate_prompt_tokens_chain(
            self.provider,
            self.model,
            messages,
            self.get_tool_definitions(),
        )
        context_limit = self.context_window_tokens if context_window_tokens is None else context_window_tokens
        runtime_margin = (
            self.runtime_margin_tokens if runtime_margin_tokens is None else max(0, int(runtime_margin_tokens))
        )
        max_prompt = max(0, context_limit - reserved_output - runtime_margin)
        warnings: list[str] = []
        decisions = self._initial_decisions(
            session_messages,
            canon,
            protected_ids,
            priority_scores or {},
        )
        decision_by_ids = {
            tuple(item.get("message_ids", [])): item
            for item in decisions
            if item.get("message_ids")
        }
        trimmed_ids = list(canon)
        while estimated > max_prompt and trimmed_ids:
            groups = [
                group
                for group in self._turn_groups(session_messages, trimmed_ids)
                if not any(mid in protected_ids for mid in group)
            ]
            if not groups:
                break
            scores = priority_scores or {}
            dropped = min(
                groups,
                key=lambda group: (
                    max((scores.get(mid, 0.0) for mid in group), default=0.0),
                    max(group),
                ),
            )
            dropped_set = set(dropped)
            trimmed_ids = [mid for mid in trimmed_ids if mid not in dropped_set]
            trimmed_ids = self.canonical_ids(session_messages, trimmed_ids)
            warnings.append(f"dropped turn messages {dropped} to fit budget")
            dropped_key = tuple(dropped)
            decision = decision_by_ids.get(dropped_key)
            if decision is not None:
                decision["decision"] = "DROP"
                decision["reason"] = "lowest-priority deletable Turn under the input budget"
            history = self.history_from_ids(session_messages, trimmed_ids)
            messages = build_messages(history)
            estimated, source = estimate_prompt_tokens_chain(
                self.provider,
                self.model,
                messages,
                self.get_tool_definitions(),
            )

        return messages, TrimOutcome(
            history=history,
            included_ids=trimmed_ids,
            estimated_tokens=estimated,
            max_prompt_tokens=max_prompt,
            source=source,
            warnings=warnings,
            decisions=decisions[:256],
        )

    @staticmethod
    def _initial_decisions(
        messages: list[dict[str, Any]],
        selected_ids: list[int],
        protected_ids: set[int],
        priority_scores: dict[int, float],
    ) -> list[dict[str, Any]]:
        """Create bounded group-level evidence for the initial Context plan."""
        decisions: list[dict[str, Any]] = []
        selected_set = set(selected_ids)
        groups = HistoryTrimmer._turn_groups(messages, selected_ids)
        recent_cutoff = max(0, len(messages) - 8)
        for group in groups:
            roles = {messages[mid].get("role") for mid in group}
            if "tool" in roles:
                layer = "L5-tool-observation"
                source = f"session_tool_exchange[{group[0]}:{group[-1]}]"
            elif any(messages[mid].get("tool_calls") for mid in group):
                layer = "L5-tool-call"
                source = f"session_tool_call[{group[0]}:{group[-1]}]"
            elif max(group, default=-1) >= recent_cutoff:
                layer = "L4-recent-history"
                source = f"session_recent_turn[{group[0]}:{group[-1]}]"
            else:
                layer = "L5-old-history"
                source = f"session_old_turn[{group[0]}:{group[-1]}]"
            protected = any(mid in protected_ids for mid in group)
            decisions.append(
                ContextDecision(
                    source=source,
                    layer=layer,
                    estimated_tokens=sum(estimate_message_tokens(messages[mid]) for mid in group),
                    priority=max((priority_scores.get(mid, 0.0) for mid in group), default=0.0),
                    decision="KEEP",
                    reason="protected by runtime policy" if protected else "selected by Context plan",
                    protected=protected,
                    message_ids=tuple(group),
                ).to_dict()
            )

        unselected = [mid for mid in range(len(messages)) if mid not in selected_set]
        if unselected:
            decisions.append(
                ContextDecision(
                    source="session_not_selected",
                    layer="L5-old-history",
                    estimated_tokens=sum(estimate_message_tokens(messages[mid]) for mid in unselected),
                    priority=0.0,
                    decision="DROP",
                    reason="not present in the candidate Context plan",
                    message_ids=tuple(unselected[:64]),
                ).to_dict()
            )
        return decisions


@dataclass
class RuntimeTrimOutcome:
    """Result of fitting an in-flight AgentLoop message list."""

    messages: list[dict[str, Any]]
    estimated_tokens: int
    input_context_budget: int
    source: str
    compacted_tool_call_ids: list[str] = field(default_factory=list)
    dropped_history_positions: list[int] = field(default_factory=list)
    removed_before_turn: int = 0
    original_tool_contents: dict[str, str] = field(default_factory=dict)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.compacted_tool_call_ids or self.dropped_history_positions)

    @property
    def over_by(self) -> int:
        return max(0, self.estimated_tokens - self.input_context_budget)


class RuntimeContextTrimmer:
    """Bound the live message list as Tool calls grow during one Turn.

    This is deliberately separate from Session compaction.  It only changes
    the provider-facing copy, preserves ToolCall/ToolResult records, and
    returns original Tool text so the caller can restore it before Session
    persistence.  Successful duplicate observations are compacted first;
    failures retain an excerpt and are never deduplicated as successes.
    """

    _KEEP_RECENT_TOOL_RESULTS = 3
    _COMPACTED_MARKER = "[Tool observation compacted"

    def __init__(
        self,
        provider: Any,
        model: str | None,
        get_tool_definitions: Callable[[], list[dict[str, Any]]],
        *,
        context_limit: int,
        reserved_output: int,
        runtime_margin: int,
    ) -> None:
        self.provider = provider
        self.model = model
        self.get_tool_definitions = get_tool_definitions
        self.context_limit = max(0, int(context_limit))
        self.reserved_output = max(0, int(reserved_output))
        self.runtime_margin = max(0, int(runtime_margin))
        self.input_context_budget = max(0, self.context_limit - self.reserved_output - self.runtime_margin)

    def _estimate(self, messages: list[dict[str, Any]]) -> tuple[int, str]:
        return estimate_prompt_tokens_chain(
            self.provider,
            self.model,
            messages,
            self.get_tool_definitions(),
        )

    def fit(
        self,
        messages: list[dict[str, Any]],
        *,
        turn_start_idx: int,
        observation_meta: dict[str, dict[str, Any]] | None = None,
        protected_positions: set[int] | None = None,
    ) -> RuntimeTrimOutcome:
        working = [dict(message) for message in messages]
        # Keep a stable identity for positions reported to the caller.  A
        # later drop operates on the already-shortened working list, but the
        # AgentLoop receives one outcome and must not interpret those shifted
        # positions as if they were all indexes in the original list.
        working_positions = list(range(len(working)))
        observation_meta = {} if observation_meta is None else observation_meta
        protected = {0, max(0, min(turn_start_idx, len(working) - 1))}
        protected.update(protected_positions or set())
        estimated, source = self._estimate(working)
        decisions: list[dict[str, Any]] = []
        compacted: list[str] = []
        dropped: list[int] = []
        removed_before_turn = 0
        original_contents: dict[str, str] = {}
        warnings: list[str] = []

        if estimated > self.input_context_budget:
            tool_indices = [i for i, msg in enumerate(working) if msg.get("role") == "tool"]
            candidates = self._tool_candidates(tool_indices, working, observation_meta)
            for index in candidates:
                message = working[index]
                content = message.get("content")
                call_id = str(message.get("tool_call_id", ""))
                if not call_id or not isinstance(content, str) or self._is_compacted(content):
                    continue
                original_contents.setdefault(call_id, content)
                info = observation_meta.get(call_id, {})
                failed = bool(info.get("failed", False))
                duplicate = bool(info.get("duplicate", False))
                message["content"] = self._compact_content(
                    content,
                    tool_name=str(message.get("name") or info.get("name") or "tool"),
                    failed=failed,
                    duplicate=duplicate,
                    fingerprint=str(info.get("fingerprint") or "")[:16],
                )
                compacted.append(call_id)
                decisions.append(
                    ContextDecision(
                        source=f"tool_result:{call_id}",
                        layer="L5-tool-observation",
                        estimated_tokens=estimate_message_tokens(message),
                        priority=1.0 if failed else 0.5,
                        decision="COMPACT",
                        reason=(
                            "preserve failure excerpt while bounding live Tool output"
                            if failed
                            else "bound old or repeated successful Tool output"
                        ),
                        protected=failed and index >= turn_start_idx,
                        message_ids=(index,),
                    ).to_dict()
                )
                estimated, source = self._estimate(working)
                if estimated <= self.input_context_budget:
                    break

        if estimated > self.input_context_budget:
            groups = self._history_groups(working, min(turn_start_idx, len(working)))
            while estimated > self.input_context_budget and groups:
                # Recompute after every deletion because the live list and
                # the User-boundary indexes shift when an older group goes
                # away.  The latest failed group must remain protected in its
                # current coordinates for the whole fit operation.
                recent_failure_positions = self._recent_failure_positions(
                    working,
                    observation_meta,
                    groups,
                )
                candidates = [
                    group
                    for group in groups
                    if not (set(group) & protected) and not (set(group) & recent_failure_positions)
                ]
                if not candidates:
                    break
                dropped_group = min(
                    candidates,
                    key=lambda group: self._history_drop_key(group, working, observation_meta),
                )
                dropped_set = set(dropped_group)
                dropped_original_group = [working_positions[index] for index in dropped_group]
                decisions.append(
                    ContextDecision(
                        source=(
                            f"live_history[{dropped_original_group[0]}:{dropped_original_group[-1]}]"
                        ),
                        layer="L5-old-history",
                        estimated_tokens=sum(estimate_message_tokens(working[i]) for i in dropped_group),
                        priority=0.0,
                        decision="DROP",
                        reason="old non-protected Turn removed after Tool observations were bounded",
                        protected=False,
                        message_ids=tuple(dropped_original_group),
                    ).to_dict()
                )
                dropped.extend(working_positions[index] for index in dropped_group)
                removed_before_turn += sum(1 for index in dropped_group if index < turn_start_idx)
                working = [message for index, message in enumerate(working) if index not in dropped_set]
                working_positions = [
                    position for index, position in enumerate(working_positions) if index not in dropped_set
                ]
                protected = {
                    position - sum(1 for index in dropped_set if index < position)
                    for position in protected
                    if position not in dropped_set
                }
                turn_start_idx -= sum(1 for index in dropped_group if index < turn_start_idx)
                groups = self._history_groups(working, min(turn_start_idx, len(working)))
                estimated, source = self._estimate(working)
                warnings.append(f"dropped live history positions {dropped_group} to fit budget")

        structural_errors = HistoryTrimmer.structural_errors(working)
        if structural_errors:
            raise ContextBudgetError(
                estimated_tokens=estimated,
                input_context_budget=self.input_context_budget,
                context_limit=self.context_limit,
                reserved_output=self.reserved_output,
                runtime_margin=self.runtime_margin,
                reason="tool_pair_integrity",
            )
        if estimated > self.input_context_budget:
            raise ContextBudgetError(
                estimated_tokens=estimated,
                input_context_budget=self.input_context_budget,
                context_limit=self.context_limit,
                reserved_output=self.reserved_output,
                runtime_margin=self.runtime_margin,
                reason="protected_runtime_context_exceeds_budget",
            )

        return RuntimeTrimOutcome(
            messages=working if (compacted or dropped) else messages,
            estimated_tokens=estimated,
            input_context_budget=self.input_context_budget,
            source=source,
            compacted_tool_call_ids=compacted,
            dropped_history_positions=dropped,
            removed_before_turn=removed_before_turn,
            original_tool_contents=original_contents,
            decisions=decisions[:256],
            warnings=warnings,
        )

    @classmethod
    def _tool_candidates(
        cls,
        tool_indices: list[int],
        messages: list[dict[str, Any]],
        observation_meta: dict[str, dict[str, Any]],
    ) -> list[int]:
        latest_by_fingerprint: dict[str, int] = {}
        for index in reversed(tool_indices):
            call_id = str(messages[index].get("tool_call_id", ""))
            fingerprint = str(observation_meta.get(call_id, {}).get("fingerprint") or "")
            if fingerprint and fingerprint not in latest_by_fingerprint:
                latest_by_fingerprint[fingerprint] = index
        recent = set(tool_indices[-cls._KEEP_RECENT_TOOL_RESULTS :])
        ranked: list[tuple[int, int]] = []
        for index in tool_indices:
            call_id = str(messages[index].get("tool_call_id", ""))
            info = observation_meta.get(call_id, {})
            failed = bool(info.get("failed", False))
            fingerprint = str(info.get("fingerprint") or "")
            duplicate = bool(
                not failed
                and fingerprint
                and latest_by_fingerprint.get(fingerprint) != index
                and latest_by_fingerprint.get(fingerprint) is not None
            )
            if duplicate and not failed:
                rank = 0
            elif not failed and index not in recent:
                rank = 1
            elif failed and index not in recent:
                rank = 2
            elif not failed:
                rank = 3
            else:
                rank = 4
            observation_meta.setdefault(call_id, {})["duplicate"] = duplicate
            ranked.append((rank, index))
        return [index for _rank, index in sorted(ranked)]

    @classmethod
    def _compact_content(
        cls,
        content: str,
        *,
        tool_name: str,
        failed: bool,
        duplicate: bool,
        fingerprint: str,
    ) -> str:
        if duplicate:
            suffix = f"; digest={fingerprint}" if fingerprint else ""
            return f"[Tool observation compacted: duplicate successful output retained later{suffix}]"
        excerpt = cls._excerpt(content, 3_200 if failed else 1_600)
        label = "failure preserved" if failed else "successful output excerpt"
        return f"[Tool observation compacted: {tool_name}; {label}]\n{excerpt}"

    @staticmethod
    def _excerpt(content: str, limit: int) -> str:
        if len(content) <= limit:
            return content
        head = max(1, int(limit * 0.65))
        tail = max(1, limit - head)
        return f"{content[:head]}\n... [middle omitted] ...\n{content[-tail:]}"

    @classmethod
    def _history_groups(cls, messages: list[dict[str, Any]], end: int) -> list[list[int]]:
        groups: list[list[int]] = []
        current: list[int] = []
        for index in range(1, max(1, end)):
            if messages[index].get("role") == "user" and current:
                groups.append(current)
                current = []
            current.append(index)
        if current:
            groups.append(current)
        return groups

    @staticmethod
    def _recent_failure_positions(
        messages: list[dict[str, Any]],
        observation_meta: dict[str, dict[str, Any]],
        groups: list[list[int]],
    ) -> set[int]:
        failed_ids = {
            str(message.get("tool_call_id", ""))
            for message in messages
            if message.get("role") == "tool"
            and observation_meta.get(str(message.get("tool_call_id", "")), {}).get("failed")
        }
        if not failed_ids:
            return set()
        latest = max(
            index
            for index, message in enumerate(messages)
            if message.get("role") == "tool" and str(message.get("tool_call_id", "")) in failed_ids
        )
        for group in reversed(groups):
            if latest in group:
                return set(group)
        return {latest}

    @staticmethod
    def _history_drop_key(
        group: list[int],
        messages: list[dict[str, Any]],
        observation_meta: dict[str, dict[str, Any]],
    ) -> tuple[int, int]:
        has_tool = any(messages[index].get("role") == "tool" for index in group)
        has_failure = any(
            messages[index].get("role") == "tool"
            and observation_meta.get(str(messages[index].get("tool_call_id", "")), {}).get("failed")
            for index in group
        )
        # Old successful Tool noise is the first class to remove; old failed
        # evidence and ordinary old Turns are retained longer.
        kind_rank = 0 if has_tool and not has_failure else 1 if has_failure else 2
        return kind_rank, max(group, default=-1)

    @classmethod
    def _is_compacted(cls, content: str) -> bool:
        return content.startswith(cls._COMPACTED_MARKER)


__all__ = ["HistoryTrimmer", "RuntimeContextTrimmer", "RuntimeTrimOutcome", "TrimOutcome"]
