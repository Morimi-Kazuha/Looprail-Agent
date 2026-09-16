from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pico.agent.spine_runner import AgentTurnRunner
from pico.spine.delivery import Capabilities, DeliveryHub
from pico.spine.events import (
    Deliverable,
    TurnEnded,
    TurnEvent,
    TurnFailed,
)
from pico.spine.runner import TurnOutcome
from pico.spine.scheduler import OriginPools, Scheduler
from pico.spine.turn import TurnRequest

from .records import DeliveryOutcome, TurnTerminalState


class RecordingOutlet:
    capabilities = Capabilities()

    def __init__(self, name: str, *, fail: bool = False) -> None:
        self.name = name
        self.fail = fail
        self.events: list[Deliverable] = []

    async def deliver(self, out: Deliverable) -> None:
        if self.fail:
            raise RuntimeError("injected delivery failure")
        self.events.append(out)


@dataclass(frozen=True)
class HostTurnObservation:
    outcome: TurnOutcome | None
    events: tuple[TurnEvent, ...]
    runtime_state: TurnTerminalState
    delivery_state: DeliveryOutcome
    failure_category: str | None
    run_id: str | None = None
    turn_id: str | None = None
    trace_artifact_ref: str | None = None
    trace_summary: dict[str, Any] | None = None


class RuntimeTrialHost:
    def __init__(
        self,
        *,
        assembly: Any,
        outlet: RecordingOutlet,
        user_concurrency: int = 1,
        system_concurrency: int = 1,
        delivery_retries: int = 0,
    ) -> None:
        self.assembly = assembly
        self.outlet = outlet
        self.runner = AgentTurnRunner(assembly.agent_loop, stream=False)
        self.hub = DeliveryHub(
            send_max_retries=delivery_retries,
            on_delivery_failure=self._delivery_failed,
        )
        self.hub.register(outlet)
        self._events: list[TurnEvent] = []
        self._delivery_failures = 0
        self.scheduler = Scheduler(
            self.runner,
            OriginPools(user=user_concurrency, system=system_concurrency),
            self._sink,
        )
        self._closed = False

    @classmethod
    async def build(
        cls,
        *,
        config: Any,
        pico_config: Any,
        provider: Any,
        cron_service: Any,
        outlet: RecordingOutlet,
        context_engine_factory: Any = None,
        user_concurrency: int = 1,
        system_concurrency: int = 1,
        delivery_retries: int = 0,
    ) -> RuntimeTrialHost:
        from pico.cli._runtime_assembly import assemble_runtime

        assembly = assemble_runtime(
            config,
            pico_config,
            provider=provider,
            cron_service=cron_service,
            interactive=False,
            context_engine_factory=context_engine_factory,
        )
        start_backend = getattr(assembly, "start_memory_backend", None)
        if start_backend is not None:
            await start_backend()
        return cls(
            assembly=assembly,
            outlet=outlet,
            user_concurrency=user_concurrency,
            system_concurrency=system_concurrency,
            delivery_retries=delivery_retries,
        )

    async def _delivery_failed(self, _notice) -> None:
        self._delivery_failures += 1

    async def _sink(self, event: TurnEvent) -> None:
        self._events.append(event)
        if not isinstance(event, (TurnEnded, TurnFailed)):
            from pico.spine.events import TurnStarted

            if not isinstance(event, TurnStarted):
                await self.hub.dispatch(event)

    async def run(self, request: TurnRequest) -> HostTurnObservation:
        start = len(self._events)
        delivered_start = len(self.outlet.events)
        failures_start = self._delivery_failures
        outcome = await self.scheduler.submit(request).result()
        await self.hub.wait_idle(request.source.channel)
        events = tuple(self._events[start:])
        terminal = _runtime_state(events, outcome)
        failure_category = _failure_category(events)
        if request.source.channel != self.outlet.name:
            delivery = DeliveryOutcome.NO_OUTLET
        elif self._delivery_failures > failures_start:
            delivery = DeliveryOutcome.DROPPED
        elif len(self.outlet.events) > delivered_start:
            delivery = DeliveryOutcome.DELIVERED
        else:
            delivery = DeliveryOutcome.DROPPED
        # Correlation is observational and lives in the durable trace index;
        # keep TurnOutcome's frozen public shape unchanged for lightweight
        # runners and existing scheduler callers.
        run_id = None
        turn_id = request.turn_id
        trace_artifact_ref = None
        trace_summary = None
        # A failed/cancelled Scheduler path returns no outcome. Resolve its
        # run from the durable root-span index so the Trial can still point at
        # execution evidence; the terminal status remains owned by lifecycle
        # events and the runtime outcome classifier above.
        try:
            from pico.tracing import config as trace_config
            from pico.tracing import trace as trace_api
            from pico.tracing.store import TraceStore

            if trace_api.enabled():
                trace_store = TraceStore(trace_config.state_dir())
                run_id = run_id or trace_store.find_run_id(turn_id)
                if run_id:
                    trace_artifact_ref = trace_store.trace_artifact_ref(run_id)
                    trace_summary = trace_store.trace_summary(run_id)
        except Exception:
            # Trace is observational. A missing optional summary must not
            # change the host's already-determined task/runtime result.
            pass
        return HostTurnObservation(
            outcome=outcome,
            events=events,
            runtime_state=terminal,
            delivery_state=delivery,
            failure_category=failure_category,
            run_id=run_id,
            turn_id=turn_id,
            trace_artifact_ref=trace_artifact_ref,
            trace_summary=trace_summary,
        )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self.scheduler.shutdown(grace=5.0)
            await self.hub.wait_idle(self.outlet.name)
        finally:
            await self.hub.aclose()
            await self.assembly.close()


def _runtime_state(
    events: tuple[TurnEvent, ...],
    outcome: TurnOutcome | None,
) -> TurnTerminalState:
    failure = next((event for event in reversed(events) if isinstance(event, TurnFailed)), None)
    if failure is not None:
        if failure.cancelled:
            return TurnTerminalState.CANCELLED
        if failure.error.startswith("provider_error:"):
            return TurnTerminalState.PROVIDER_FAILED
        return TurnTerminalState.ERROR
    if outcome is not None and outcome.tool_failures:
        return TurnTerminalState.COMPLETED_WITH_TOOL_FAILURE
    return TurnTerminalState.COMPLETED


def _failure_category(
    events: tuple[TurnEvent, ...],
) -> str | None:
    failure = next((event for event in reversed(events) if isinstance(event, TurnFailed)), None)
    if failure is None:
        return None
    if failure.cancelled:
        return "cancelled"
    prefix = "provider_error:"
    if failure.error.startswith(prefix):
        return failure.error.removeprefix(prefix)
    return "runtime_error"
