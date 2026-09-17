from __future__ import annotations

from looprail.spine import ChatType, Origin, OriginPools, Scheduler, Source, TurnOutcome, TurnRequest, Usage


def _request(turn_id: str | None = None) -> TurnRequest:
    source = Source(channel="test", chat_id="chat", sender_id="user", chat_type=ChatType.DM)
    return TurnRequest(origin=Origin.USER, source=source, text="hello", turn_id=turn_id)


class _RecordingRunner:
    def __init__(self) -> None:
        self.turn_ids: list[str | None] = []

    async def run(self, req, emit, drain) -> TurnOutcome:
        self.turn_ids.append(req.turn_id)
        return TurnOutcome(usage=Usage(0, 0, 0), explicit_reply=False)


async def _sink(event) -> None:
    pass


async def test_scheduler_assigns_one_core_turn_id_and_preserves_request_identity() -> None:
    runner = _RecordingRunner()
    scheduler = Scheduler(runner, OriginPools(user=1, system=1), _sink)
    request = _request()

    await scheduler.submit(request).result()
    assert request.turn_id is not None
    assert request.turn_id.startswith("turn-")

    assert runner.turn_ids == [request.turn_id]


async def test_scheduler_preserves_an_existing_core_turn_id() -> None:
    runner = _RecordingRunner()
    scheduler = Scheduler(runner, OriginPools(user=1, system=1), _sink)
    request = _request("turn-existing")

    await scheduler.submit(request).result()

    assert request.turn_id == "turn-existing"
    assert runner.turn_ids == ["turn-existing"]
