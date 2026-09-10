"""管理 Tool 的动态注册、参数门禁、执行观察与安全并发。

`ToolRegistry` 是模型 function name 到具体 Tool 实现的唯一目录。它把定义暴露给 Provider，
执行时统一完成类型转换、Schema 校验、超时、异常与 `ToolResult.failed` 归一化；批量调用只让
``READ + concurrency_safe`` 的连续段并行，其余副作用调用保持原顺序。Registry 不判断 Tool
业务权限，也不把 Tool Result 当成用户任务完成证据。
"""

import asyncio
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import replace
from typing import Any

from loguru import logger

from pico.agent.effects import (
    EffectClass,
    EffectJournal,
    EffectRecord,
    EffectStatus,
    canonical_digest,
    classify_effect,
    local_write_evidence,
    local_write_precondition_matches,
    new_effect_id,
    observe_local_write,
    utc_now,
)
from pico.agent.tools.base import Tool, ToolResult
from pico.agent.tools.execution import (
    ToolEffect,
    ToolExecution,
    ToolExecutionContext,
    ToolInvocation,
)
from pico.tracing import semconv, trace

ToolStartCallback = Callable[[ToolInvocation], Awaitable[None]]
ToolCompleteCallback = Callable[[ToolExecution], Awaitable[None]]
EffectCrashHook = Callable[[str, EffectRecord], None]


class ToolRegistry:
    """提供 Agent Tool 动态注册、查找、定义导出与受控执行的注册表。

    实例按 Tool ``name`` 保存实现，可在启动、Plugin 激活或 MCP 连接时增删。`execute` 形成单
    调用安全边界，`execute_many` 根据 capability 将连续安全 Read 分批并行，同时通过
    on_start/on_complete 保持 ToolEvent 观察。默认并发为 4，未自设超时的 Tool 最多运行 300
    秒；Ask User 等 ``blocking_interaction`` 不套该上限。

    注册表长期由 AgentLoop 持有，但 ToolExecutionContext 按调用携带 call_id、Session、迭代与
    Origin，不能把一次 Turn 的观察状态放进 Registry 全局字段。
    """

    # 为未设置自身 ``timeout_seconds`` 的工具提供兜底上限。刻意设得宽裕：它用于打断
    # 没有内部超时且永不返回的无限挂起，而不是强制严格的单工具 SLA。
    DEFAULT_TOOL_TIMEOUT_S = 300.0
    DEFAULT_MAX_PARALLEL = 4

    def __init__(
        self,
        *,
        max_parallel: int = DEFAULT_MAX_PARALLEL,
        effect_journal: EffectJournal | None = None,
        crash_hook: EffectCrashHook | None = None,
    ):
        if max_parallel < 1:
            raise ValueError("max_parallel must be positive")
        self._tools: dict[str, Tool] = {}
        self._max_parallel = max_parallel
        self._effect_journal = effect_journal
        self._effect_crash_hook = crash_hook

    @property
    def effect_journal(self) -> EffectJournal | None:
        """Return the optional durable fact store used by this registry."""

        return self._effect_journal

    def _new_effect_record(
        self,
        invocation: ToolInvocation,
        tool: Tool,
        params: dict[str, Any],
    ) -> EffectRecord:
        current_trace = trace.current()
        precondition = postcondition = None
        if classify_effect(invocation.name, tool.capability) is EffectClass.LOCAL_WRITE:
            try:
                evidence = local_write_evidence(tool, params)
            except Exception:  # noqa: BLE001 — evidence failure must not expose file internals
                evidence = None
            if evidence is not None:
                precondition, postcondition = evidence
        turn_id = invocation.context.turn_id or getattr(current_trace, "turn_id", None)
        session_key = invocation.context.session_key or getattr(current_trace, "session_key", None)
        return EffectRecord(
            effect_id=new_effect_id(),
            turn_id=turn_id,
            session_key=session_key,
            trace_id=current_trace.trace_id if current_trace is not None else None,
            tool_call_id=invocation.context.call_id,
            tool_name=invocation.name,
            effect_class=classify_effect(invocation.name, tool.capability),
            arguments_digest=canonical_digest(params),
            status=EffectStatus.PREPARED,
            prepared_at=utc_now(),
            precondition=precondition,
            postcondition=postcondition,
        )

    def _effect_hook(self, stage: str, record: EffectRecord) -> None:
        if self._effect_crash_hook is not None:
            self._effect_crash_hook(stage, record)

    @staticmethod
    def _annotate_result(result: ToolResult, effect_id: str | None) -> ToolResult:
        if effect_id is None:
            return result
        try:
            result.effect_id = effect_id
            return result
        except AttributeError:
            return ToolResult(result, failed=bool(getattr(result, "failed", False)))

    @staticmethod
    def _terminal_status(record: EffectRecord, result: ToolResult) -> tuple[EffectStatus, str | None]:
        failed = bool(getattr(result, "failed", False))
        if record.effect_class is EffectClass.READ:
            return (EffectStatus.FAILED if failed else EffectStatus.COMMITTED, "ToolResultFailed" if failed else None)
        if record.effect_class is EffectClass.LOCAL_WRITE:
            evidence = observe_local_write(record)
            current_hash = evidence.get("current_hash")
            expected_post = (record.postcondition or {}).get("expected_post_hash")
            expected_pre = (record.precondition or {}).get("expected_pre_hash")
            if expected_post and current_hash == expected_post:
                return EffectStatus.COMMITTED, None
            precondition = record.precondition or {}
            expected_pre_exists = precondition.get("expected_exists")
            pre_matches = (
                current_hash == expected_pre
                and (
                    expected_pre is not None
                    or (expected_pre_exists is False and evidence.get("current_exists") is not True)
                )
                and (expected_pre_exists is None or evidence.get("current_exists") == expected_pre_exists)
            )
            if failed and pre_matches:
                return EffectStatus.FAILED, "ToolResultFailed"
            return EffectStatus.UNKNOWN, "ToolResultFailed" if failed else "PostconditionMismatch"
        # A normal return is a local observation that the call boundary returned, but
        # an error return cannot prove whether an opaque side effect happened.
        if failed:
            return EffectStatus.UNKNOWN, "ToolResultFailed"
        return EffectStatus.COMMITTED, None

    def register(self, tool: Tool, *, replace: bool = False) -> None:
        """按稳定名称注册一个 Tool，并显式控制同名覆盖。

        名称已存在且 ``replace=False`` 时抛 `ValueError`，防止内置能力被意外替换；Plugin 等
        有意覆盖路径必须传 ``replace=True``。成功后字典中的实现立即用于定义导出和 dispatch，
        方法不复制 Tool，也不自动应用 disabled list。
        """
        if tool.name in self._tools and not replace:
            raise ValueError(f"Tool '{tool.name}' is already registered")
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        """按 ``name`` 移除 Tool，名称不存在时保持 no-op。

        移除只影响之后的 definitions 与执行查找，不取消已经启动的 Tool task，也不销毁 Tool
        自有资源。该幂等语义使宽泛 disabled list 可以安全覆盖不同构建的注册表。
        """
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        """返回 ``name`` 对应的 Tool 实例，不存在时返回 ``None``。

        调用方可据此做类型特定接线，例如设置 MessageTool Context；返回的是注册表持有的原
        实例而非副本。查询不执行 Tool，也不改变注册顺序。
        """
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        """判断指定 Tool 名称当前是否已注册。

        结果只反映目录存在性，不代表 Tool 对本轮可见、参数有效或外部依赖健康；Tool Search
        与 disabled 策略可能在更高层继续缩小模型可见集合。
        """
        return name in self._tools

    def get_definitions(self) -> list[dict[str, Any]]:
        """按注册顺序返回全部 Tool 的 OpenAI function definitions。

        每项由 `Tool.to_schema()` 现时生成，因此反映当前 Registry 内容。返回值用于 Provider
        请求与 Token 预算；它只描述能力，不包含 Tool 实例或执行 Context，也不应用额外搜索
        策略过滤。
        """
        return [tool.to_schema() for tool in self._tools.values()]

    @trace.instrument("tool.call", extract=semconv.tool_call)
    async def execute(
        self,
        name: str,
        params: dict[str, Any],
        call_id: str | None = None,
        context: ToolExecutionContext | None = None,
    ) -> ToolResult:
        """按名称和参数执行一次 Tool，并把所有可预期失败规范化为 `ToolResult`。

        ``call_id`` 是模型自己的 tool-call id，不参与 dispatch；它只写入
        ``tool.call_id``/ToolExecutionContext，使 tracing span 与同一调用发出的 ToolEvent 关联。
        未找到名称时返回含 Available 列表的失败结果。找到后依次执行 schema cast、validation，
        再调用 `execute_with_context`。

        普通 Tool 由 `asyncio.wait_for` 应用自身 ``timeout_seconds`` 或 Registry 300 秒上限；
        ``blocking_interaction`` 有意等待人类，不套统一超时。显式 ToolResult 原样返回，以
        ``Error`` 开头的普通字符串和异常/timeout 会追加“分析错误并换方法”提示并标为失败。
        方法永不因业务异常向模型暴露 Python stack，但 asyncio 取消仍遵循 Task 语义。
        """
        _hint = "\n\n[Analyze the error above and try a different approach.]"

        invocation = ToolInvocation(
            name=name,
            arguments=params,
            context=context or ToolExecutionContext(call_id=call_id),
        )
        tool = self._tools.get(name)
        if not tool:
            return ToolResult(
                f"Error: Tool '{name}' not found. Available: {', '.join(self.tool_names)}",
                failed=True,
            )

        try:
            # 尝试转换参数以匹配模式类型，再执行同一份 Schema 校验。
            params = tool.cast_params(params)
            errors = tool.validate_params(params)
            if errors:
                return ToolResult(
                    f"Error: Invalid parameters for tool '{name}': " + "; ".join(errors) + _hint,
                    failed=True,
                )
        except Exception as e:
            return ToolResult(f"Error executing {name}: {str(e)}" + _hint, failed=True)

        ceiling = tool.timeout_seconds or self.DEFAULT_TOOL_TIMEOUT_S
        effect_record: EffectRecord | None = None
        running_record: EffectRecord | None = None
        effect_invocation = invocation
        if self._effect_journal is not None:
            effect_record = self._new_effect_record(invocation, tool, params)
            try:
                # This is the fail-closed boundary: no tool code runs until PREPARED
                # and RUNNING are both durable facts.
                self._effect_journal.append(effect_record)
            except Exception:
                logger.exception("Effect PREPARED could not be journaled for {}", name)
                return self._annotate_result(
                    ToolResult("Error: effect journal unavailable; tool was not executed." + _hint, failed=True),
                    effect_record.effect_id,
                )
            self._effect_hook("prepared", effect_record)
            running_record = effect_record.with_updates(status=EffectStatus.RUNNING, started_at=utc_now())
            try:
                self._effect_journal.append(running_record)
            except Exception:
                logger.exception("Effect RUNNING could not be journaled for {}", name)
                return self._annotate_result(
                    ToolResult("Error: effect journal unavailable; tool was not executed." + _hint, failed=True),
                    effect_record.effect_id,
                )
            self._effect_hook("running", running_record)
            if effect_record.effect_class is EffectClass.LOCAL_WRITE:
                observed = observe_local_write(running_record)
                if not local_write_precondition_matches(running_record, observed):
                    terminal = running_record.with_updates(
                        status=EffectStatus.FAILED,
                        finished_at=utc_now(),
                        error_class="PreconditionConflict",
                    )
                    # A crash hook here intentionally leaves RUNNING durable.  A
                    # normal conflict is terminal only after that fact is journaled.
                    self._effect_hook("before_terminal", terminal)
                    try:
                        self._effect_journal.append(terminal)
                    except Exception:
                        logger.exception("Effect precondition failure could not be journaled for {}", name)
                        return self._annotate_result(
                            ToolResult(
                                "Error: write precondition changed and its outcome is unknown." + _hint,
                                failed=True,
                            ),
                            running_record.effect_id,
                        )
                    self._effect_hook("terminal", terminal)
                    return self._annotate_result(
                        ToolResult(
                            "Error: write precondition changed; file was not modified. Resolve the conflict and retry."
                            + _hint,
                            failed=True,
                        ),
                        running_record.effect_id,
                    )
            effect_invocation = replace(
                invocation,
                context=replace(invocation.context, effect_id=effect_record.effect_id),
            )

        try:
            if tool.blocking_interaction:
                # 该工具有意等待人类，不得被超时计时器终止。
                result = await tool.execute_with_context(effect_invocation.context, **params)
            else:
                result = await asyncio.wait_for(
                    tool.execute_with_context(effect_invocation.context, **params),
                    timeout=ceiling,
                )

            if isinstance(result, ToolResult):
                normalized = result
            elif isinstance(result, str) and result.startswith("Error"):
                normalized = ToolResult(result + _hint, failed=True)
            else:
                normalized = ToolResult(result)
        except asyncio.CancelledError:
            if running_record is not None and self._effect_journal is not None:
                unknown = running_record.with_updates(
                    status=EffectStatus.UNKNOWN,
                    finished_at=utc_now(),
                    error_class="CancelledError",
                )
                try:
                    self._effect_hook("before_terminal", unknown)
                    self._effect_journal.append(unknown)
                    self._effect_hook("terminal", unknown)
                except Exception:
                    logger.exception("Effect UNKNOWN could not be journaled for {}", name)
            raise
        except asyncio.TimeoutError as exc:
            normalized = ToolResult(
                f"Error: Tool '{name}' timed out after {ceiling:.0f}s." + _hint,
                failed=True,
            )
            execution_error = type(exc).__name__
        except Exception as exc:
            normalized = ToolResult(f"Error executing {name}: {str(exc)}" + _hint, failed=True)
            execution_error = type(exc).__name__
        else:
            execution_error = None

        if running_record is not None and self._effect_journal is not None:
            terminal_status, inferred_error = self._terminal_status(running_record, normalized)
            terminal = running_record.with_updates(
                status=terminal_status,
                finished_at=utc_now(),
                error_class=execution_error or inferred_error,
            )
            # A crash here intentionally leaves RUNNING durable. Recovery can then
            # inspect LOCAL_WRITE postcondition evidence instead of replaying blindly.
            self._effect_hook("before_terminal", terminal)
            try:
                self._effect_journal.append(terminal)
            except Exception:
                logger.exception("Effect terminal state could not be journaled for {}", name)
                return self._annotate_result(
                    ToolResult("Error: tool outcome is unknown because the journal is unavailable." + _hint, failed=True),
                    running_record.effect_id,
                )
            self._effect_hook("terminal", terminal)

        return self._annotate_result(normalized, effect_record.effect_id if effect_record is not None else None)

    async def execute_invocation(self, invocation: ToolInvocation) -> ToolExecution:
        started = time.perf_counter_ns()
        result = await self.execute(
            invocation.name,
            invocation.arguments,
            invocation.context.call_id,
            invocation.context,
        )
        duration_ms = (time.perf_counter_ns() - started) / 1_000_000
        effect_id = getattr(result, "effect_id", None)
        if effect_id is not None:
            invocation = replace(invocation, context=replace(invocation.context, effect_id=effect_id))
        return ToolExecution(invocation=invocation, result=result, duration_ms=duration_ms, effect_id=effect_id)

    async def execute_many(
        self,
        invocations: Iterable[ToolInvocation],
        *,
        parallel_safe: bool = True,
        on_start: ToolStartCallback | None = None,
        on_complete: ToolCompleteCallback | None = None,
    ) -> list[ToolExecution]:
        pending = list(invocations)
        executions: list[ToolExecution] = []
        index = 0
        while index < len(pending):
            if parallel_safe and self._is_concurrency_safe(pending[index]):
                end = index + 1
                while end < len(pending) and self._is_concurrency_safe(pending[end]):
                    end += 1
                for start in range(index, end, self._max_parallel):
                    executions.extend(
                        await self._execute_parallel(
                            pending[start : min(start + self._max_parallel, end)],
                            on_start,
                            on_complete,
                        )
                    )
                index = end
                continue
            executions.append(await self._execute_observed(pending[index], on_start, on_complete))
            index += 1
        return executions

    def _is_concurrency_safe(self, invocation: ToolInvocation) -> bool:
        resolved = self._resolve_invocation(invocation)
        tool = self._tools.get(resolved.name)
        return bool(tool is not None and tool.capability.effect is ToolEffect.READ and tool.capability.concurrency_safe)

    def _resolve_invocation(self, invocation: ToolInvocation) -> ToolInvocation:
        tool = self._tools.get(invocation.name)
        if tool is None:
            return invocation
        return tool.resolve_invocation(invocation)

    async def _execute_parallel(
        self,
        invocations: list[ToolInvocation],
        on_start: ToolStartCallback | None,
        on_complete: ToolCompleteCallback | None,
    ) -> list[ToolExecution]:
        tasks = [asyncio.create_task(self._execute_observed(item, on_start, on_complete)) for item in invocations]
        try:
            return await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    async def _execute_observed(
        self,
        invocation: ToolInvocation,
        on_start: ToolStartCallback | None,
        on_complete: ToolCompleteCallback | None,
    ) -> ToolExecution:
        observed = self._resolve_invocation(invocation)
        if on_start is not None:
            await on_start(observed)
        execution = await self.execute_invocation(invocation)
        if execution.invocation is not observed:
            observed = replace(
                observed,
                context=replace(observed.context, effect_id=execution.effect_id),
            )
            execution = replace(execution, invocation=observed)
        if on_complete is not None:
            await on_complete(execution)
        return execution

    @property
    def tool_names(self) -> list[str]:
        """按当前注册顺序返回 Tool 名称列表。

        返回新 list，调用方修改它不会影响 Registry；顺序与 `get_definitions` 一致，可用于未知
        Tool 错误提示和 Curator trace。列表不表示模型经 Tool Search 后的实际可见子集。
        """
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
