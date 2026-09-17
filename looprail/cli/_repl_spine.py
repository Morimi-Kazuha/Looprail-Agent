"""Spine wiring for the REPL: the runner (an AgentTurnRunner with stream=False,
so the reply is one Text), the outlet that renders a turn's text to the console,
and the sink that feeds the delivery hub.

The REPL runs turns through spine (submit -> lane -> run_turn -> hub -> outlet).
spine never imports cli; cli imports spine.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from looprail.agent.spine_runner import AgentTurnRunner
from looprail.cli._run_surface import format_tool_complete, format_tool_start
from looprail.spine import (
    ChatType,
    Deliverable,
    Notice,
    NoticeKind,
    Origin,
    OriginPools,
    Scheduler,
    Source,
    Text,
    ToolEvent,
    ToolPhase,
    TurnEvent,
    TurnFailed,
    TurnHandle,
    TurnRequest,
)
from looprail.spine.delivery import Capabilities, DeliveryHub, make_hub_sink
from looprail.spine.events import Reasoning
from looprail.spine.teardown import teardown_spine


class CliOutlet:
    """Renders a turn's deliverables to the terminal. Runs non-streaming (run_turn
    stream=False), so the reply arrives as one Text; ToolEvent is rendered only
    when the reviewer-facing run surface supplies a renderer, while MediaOut
    remains intentionally unsupported here.

    ``render_notice`` is opt-in progress rendering: when set, Notice and
    Reasoning events render as progress lines, gated by ``send_progress``
    (PROGRESS) and ``send_tool_hints`` (TOOL_HINT). A surface that omits it eats
    those events as before."""

    def __init__(
        self,
        channel: str,
        render: Callable[[str], None],
        *,
        render_notice: Callable[[str], None] | None = None,
        render_error: Callable[[str], None] | None = None,
        render_tool: Callable[[str], None] | None = None,
        verbose_tools: bool = False,
        send_progress: bool = False,
        send_tool_hints: bool = False,
    ) -> None:
        self.name = channel
        self.capabilities = Capabilities()
        self._render = render
        self._render_notice = render_notice
        self._render_error = render_error
        self._render_tool = render_tool
        self._verbose_tools = verbose_tools
        self._send_progress = send_progress
        self._send_tool_hints = send_tool_hints
        self._tool_starts: dict[str, tuple[str, dict[str, Any] | None]] = {}

    async def deliver(self, out: Deliverable) -> None:
        if isinstance(out, Text):
            self._render(out.content)
        elif isinstance(out, Notice) and self._render_notice is not None:
            if out.kind is NoticeKind.PROGRESS and self._send_progress:
                self._render_notice(out.detail or "")
            elif out.kind is NoticeKind.TOOL_HINT and self._send_tool_hints:
                self._render_notice(out.detail or "")
        elif isinstance(out, Reasoning):
            if self._render_notice is not None and self._send_progress and out.content:
                self._render_notice(out.content)
        elif isinstance(out, ToolEvent):
            if self._render_tool is None:
                if out.phase is ToolPhase.COMPLETE and out.failed and self._render_error is not None:
                    self._render_error(f"Tool failed: {out.result_preview}")
                return
            if out.phase is ToolPhase.START:
                self._tool_starts[out.tool_call_id] = (out.name, out.arguments)
                self._render_tool(format_tool_start(out))
            elif out.phase is ToolPhase.COMPLETE:
                start_name, start_arguments = self._tool_starts.pop(out.tool_call_id, (None, None))
                self._render_tool(
                    format_tool_complete(
                        out,
                        start_name=start_name,
                        start_arguments=start_arguments,
                        verbose=self._verbose_tools,
                    )
                )
        # 其他 Notice 类型与 MediaOut 会被吞掉（无法渲染的路径）。


def _make_cli_sink(
    hub: DeliveryHub,
    render_error: Callable[[str], None] | None,
    *,
    on_lifecycle: Callable[[TurnEvent], None] | None = None,
) -> Callable[[TurnEvent], Awaitable[None]]:
    deliver = make_hub_sink(hub)

    async def sink(event: TurnEvent) -> None:
        if on_lifecycle is not None:
            on_lifecycle(event)
        if isinstance(event, TurnFailed) and render_error is not None:
            detail = "Turn cancelled" if event.cancelled else f"Turn failed: {event.error}"
            render_error(detail)
        await deliver(event)

    return sink


def build_repl(
    agent_loop: Any,
    channel: str,
    render: Callable[[str], None],
    *,
    render_notice: Callable[[str], None] | None = None,
    render_error: Callable[[str], None] | None = None,
    render_tool: Callable[[str], None] | None = None,
    on_lifecycle: Callable[[TurnEvent], None] | None = None,
    verbose_tools: bool = False,
    send_progress: bool = False,
    send_tool_hints: bool = False,
    user_pool: int = 1,
    system_pool: int = 1,
) -> tuple[Scheduler, DeliveryHub, Callable[[], Awaitable[None]]]:
    """Wire the spine pieces a REPL turn flows through: a hub with the channel's
    CliOutlet registered, and a Scheduler whose runner bridges the agent loop and
    whose sink is that hub. Returns those plus a ``teardown`` the caller awaits on
    exit — stop the scheduler (no more events) then close the hub's outlet workers
    — shared with the test so the teardown sequence itself is covered.

    ``render_notice`` + the two config flags are threaded to the CliOutlet for the
    one-shot ``-m`` path; the interactive REPL omits them (Notice stays eaten)."""
    hub = DeliveryHub()
    hub.register(
        CliOutlet(
            channel,
            render,
            render_notice=render_notice,
            render_error=render_error,
            render_tool=render_tool,
            verbose_tools=verbose_tools,
            send_progress=send_progress,
            send_tool_hints=send_tool_hints,
        )
    )
    scheduler = Scheduler(
        AgentTurnRunner(agent_loop, stream=False),
        OriginPools(user=user_pool, system=system_pool),
        _make_cli_sink(hub, render_error, on_lifecycle=on_lifecycle),
    )

    async def teardown() -> None:
        await teardown_spine(scheduler, hub, grace=0.0)

    return scheduler, hub, teardown


async def run_repl_loop(
    read_input: Callable[[], Awaitable[str]],
    submit: Callable[[TurnRequest], TurnHandle],
    wait_idle: Callable[[str], Awaitable[None]],
    *,
    channel: str,
    chat_id: str,
    is_exit: Callable[[str], bool],
    handle_slash: Callable[[str], bool],
    thinking: Callable[[], Any],
    on_exit: Callable[[], None],
    on_submit: Callable[[TurnRequest], None] | None = None,
    after_turn: Callable[[TurnRequest, Any | None], None] | None = None,
) -> None:
    """Read a line, submit it as a turn, wait for the turn to finish AND its
    output to render, then prompt again — so a reply always lands before the next
    prompt. result() means the turn stopped emitting; wait_idle is the render
    barrier that the async outlet has caught up. tty/console and exit/slash are
    injected so this runs against the real scheduler and hub under test."""
    while True:
        # 包住整个迭代（读取和轮次），使任意时刻（包括轮次中途）的 Ctrl-C / EOF 都能像
        # 总线循环一样干净退出。
        try:
            user_input = await read_input()
            command = user_input.strip()
            if not command:
                continue
            if is_exit(command):
                on_exit()
                return
            if command.startswith("/") and handle_slash(command):
                continue
            request = TurnRequest(
                origin=Origin.USER,
                source=Source(channel=channel, chat_id=chat_id, sender_id="user", chat_type=ChatType.DM),
                text=user_input,
                conversation=f"{channel}:{chat_id}",
            )
            handle = submit(request)
            if on_submit is not None:
                on_submit(request)
            with thinking():
                outcome = await handle.result()
            await wait_idle(channel)
            if after_turn is not None:
                after_turn(request, outcome)
        except (EOFError, KeyboardInterrupt):
            on_exit()
            return
