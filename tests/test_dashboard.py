"""The dashboard stream (issue 06, ADR-0006) and the snapshot it opens with.

Two things are under test, as plain `asyncio` and as a query, and the wire is
deliberately neither of them. `stream` is a protocol — subscribe, snapshot,
deltas — whose one correctness claim is the *order* of the first two, and whose
one named risk is a subscription that outlives its client; both are checked
here with a fake snapshot and a real bus. `snapshot` is a `SELECT` whose shape
the business sees, checked against planted rows for what it carries and, per
ADR-0005, for what it must not.

The SSE framing is not parsed here. A broken stream is loud in the demo and
proved by eye and by the phone smoke test (issue 10's rationale); a test that
re-implemented the `event:`/`data:` grammar would be testing its own parser.
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from fieldproof.dashboard import (
    KEEPALIVE,
    DashboardSnapshot,
    DashboardVerdict,
    Frame,
    snapshot,
    stream,
)
from fieldproof.events import EventBus, VisitTransitioned
from fieldproof.schema import Assignment, Ping, VerdictRecord, Visit, new_assignment
from fieldproof.transitions import AssignmentState, VisitState
from fieldproof.verification import Classification, Verdict

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)

EMPTY = DashboardSnapshot(assignments=[])


async def empty() -> DashboardSnapshot:
    """A snapshot that finds nothing. The stream tests are about order, not content."""
    return EMPTY


def a_delta() -> VisitTransitioned:
    return VisitTransitioned(
        visit_id=uuid4(),
        assignment_id=uuid4(),
        from_state=VisitState.ACTIVE,
        to_state=VisitState.ABANDONED,
        at=NOW,
    )


async def pull(frames: AsyncIterator[Frame]) -> Frame:
    """The next frame, bounded, so a stream that never yields fails instead of hanging."""
    return await asyncio.wait_for(anext(frames), timeout=2.0)


# ---------------------------------------------------------------- the protocol


async def test_the_first_frame_is_the_snapshot_and_then_every_event_in_order() -> None:
    bus = EventBus()
    first, second = a_delta(), a_delta()

    frames = stream(bus, empty)
    assert await pull(frames) == EMPTY
    bus.publish([first, second])
    assert await pull(frames) == first
    assert await pull(frames) == second
    await frames.aclose()


async def test_a_delta_racing_the_snapshot_is_not_lost() -> None:
    """The order of subscribe and snapshot is the correctness argument (ADR-0006).

    The snapshot query is in flight — its `SELECT` has not returned — when a
    sweep commits a transition and publishes it. Every publisher commits first
    and publishes second, so this is the latest a transition can land and still
    be absent from the snapshot. It has to arrive as a delta, and it only does
    if the queue was registered before the query started. Snapshot-then-subscribe
    drops it on the floor, and nothing ever corrects the client.
    """
    bus = EventBus()
    delta = a_delta()

    async def snapshot_while_a_sweep_lands() -> DashboardSnapshot:
        bus.publish([delta])
        await asyncio.sleep(0)
        return EMPTY

    frames = stream(bus, snapshot_while_a_sweep_lands)
    assert await pull(frames) == EMPTY
    assert await pull(frames) == delta
    await frames.aclose()


async def test_a_quiet_stream_sends_keepalives_and_still_delivers_the_next_event() -> None:
    bus = EventBus()
    delta = a_delta()

    frames = stream(bus, empty, keepalive_s=0.01)
    assert await pull(frames) == EMPTY
    assert await pull(frames) is KEEPALIVE
    bus.publish([delta])
    assert await pull(frames) == delta
    await frames.aclose()


# ---------------------------------------------------------------- the leak, on every way out


async def test_the_subscription_closes_when_the_consumer_stops() -> None:
    bus = EventBus()
    frames = stream(bus, empty)
    await pull(frames)
    assert bus.subscribers == 1

    await frames.aclose()

    assert bus.subscribers == 0


async def test_the_subscription_closes_when_the_consumer_is_cancelled_mid_wait() -> None:
    """The route a vanished client takes: Starlette cancels the task iterating
    the generator while it is parked on the queue (`api.dashboard_stream`)."""
    bus = EventBus()
    frames = stream(bus, empty)
    await pull(frames)

    async def wait_for_a_delta() -> Frame:
        return await anext(frames)

    waiting = asyncio.create_task(wait_for_a_delta())
    await asyncio.sleep(0)
    assert bus.subscribers == 1

    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting

    assert bus.subscribers == 0


async def test_the_subscription_closes_when_the_snapshot_fails() -> None:
    bus = EventBus()

    async def database_gone() -> DashboardSnapshot:
        raise RuntimeError("connection refused")

    frames = stream(bus, database_gone)
    with pytest.raises(RuntimeError, match="connection refused"):
        await pull(frames)

    assert bus.subscribers == 0


async def test_two_dashboards_each_get_a_snapshot_and_the_same_deltas() -> None:
    bus = EventBus()
    delta = a_delta()

    one, two = stream(bus, empty), stream(bus, empty)
    assert await pull(one) == EMPTY
    assert await pull(two) == EMPTY
    bus.publish([delta])
    assert await pull(one) == delta
    assert await pull(two) == delta
    await one.aclose()
    assert bus.subscribers == 1
    await two.aclose()
    assert bus.subscribers == 0


# ---------------------------------------------------------------- the snapshot


async def make_assignment(db: AsyncSession, *, created_at: datetime = NOW) -> Assignment:
    assignment = new_assignment(
        business_name="Northwind Coffee",
        participant_name="Sam Okonjo",
        target_lat=51.5080,
        target_lng=-0.1281,
        deadline_at=created_at + timedelta(days=7),
        created_at=created_at,
    )
    db.add(assignment)
    await db.commit()
    return assignment


async def make_visit(
    db: AsyncSession,
    assignment: Assignment,
    *,
    state: VisitState,
    started_at: datetime = NOW,
    ended_at: datetime | None = None,
) -> Visit:
    visit = Visit(
        assignment_id=assignment.id,
        state=state,
        started_at=started_at,
        ended_at=ended_at,
        last_ping_at=ended_at if ended_at is not None else started_at,
        created_at=started_at,
    )
    db.add(visit)
    await db.commit()
    return visit


async def make_verdict(db: AsyncSession, visit: Visit) -> VerdictRecord:
    record = VerdictRecord(
        visit_id=visit.id,
        verdict=Verdict.VERIFIED,
        inside_s=540.0,
        outside_s=60.0,
        unattributed_s=0.0,
        attributed_total_s=600.0,
        dwell_ratio=0.9,
        conclusive_pings=41,
        total_pings=41,
        visit_duration_s=600.0,
        radius_m=100.0,
        min_duration_s=300,
        scoring_config_version="v1",
        computed_at=NOW,
    )
    db.add(record)
    await db.commit()
    return record


async def test_an_empty_dashboard(db: AsyncSession) -> None:
    assert await snapshot(db) == EMPTY


async def test_visits_nest_under_their_assignment_with_their_verdict(db: AsyncSession) -> None:
    """Oldest assignment first, oldest visit first, and the verdict on the one
    visit that has one. Two visits on one assignment is the attempt count
    issue 09 renders (ADR-0001), carried as the length of the list."""
    older = await make_assignment(db, created_at=NOW - timedelta(days=1))
    newer = await make_assignment(db, created_at=NOW)
    first_attempt = await make_visit(
        db,
        older,
        state=VisitState.ABANDONED,
        started_at=NOW - timedelta(hours=2),
        ended_at=NOW - timedelta(hours=1),
    )
    second_attempt = await make_visit(
        db,
        older,
        state=VisitState.COMPLETED,
        started_at=NOW - timedelta(minutes=30),
        ended_at=NOW - timedelta(minutes=20),
    )
    record = await make_verdict(db, second_attempt)

    result = await snapshot(db)

    assert [assignment.id for assignment in result.assignments] == [older.id, newer.id]
    attempts, none = result.assignments
    assert none.visits == []
    assert [visit.id for visit in attempts.visits] == [first_attempt.id, second_attempt.id]
    abandoned, completed = attempts.visits
    assert abandoned.state is VisitState.ABANDONED
    assert abandoned.ended_at == first_attempt.ended_at
    assert abandoned.verdict is None
    assert completed.state is VisitState.COMPLETED
    assert completed.verdict == DashboardVerdict.model_validate(record)
    assert completed.verdict.dwell_ratio == 0.9
    assert attempts.state is AssignmentState.ASSIGNED
    assert attempts.business_name == "Northwind Coffee"


async def test_a_completed_visit_without_a_verdict_still_renders(db: AsyncSession) -> None:
    """Issue 08 calls this the state the dashboard cannot render. The snapshot
    renders it anyway — as a completed visit with no verdict — rather than
    failing the whole dashboard over one row another transaction got wrong."""
    assignment = await make_assignment(db)
    visit = await make_visit(db, assignment, state=VisitState.COMPLETED, ended_at=NOW)

    (only,) = (await snapshot(db)).assignments
    (rendered,) = only.visits
    assert rendered.id == visit.id
    assert rendered.verdict is None


def keys(value: Any) -> set[str]:
    """Every key at every depth of a dumped model."""
    if isinstance(value, dict):
        return set(value) | set().union(*(keys(inner) for inner in value.values()))
    if isinstance(value, list):
        return set().union(*(keys(item) for item in value))
    return set()


LOCATION_EVIDENCE = {
    "lat",
    "lng",
    "target_lat",
    "target_lng",
    "accuracy_m",
    "distance_m",
    "classification",
    "reported_at",
    "received_at",
    "last_ping_at",
}
"""Every column that describes where a participant was, or a single ping.
`radius_m` is not here: it is a term of the assignment the business itself set,
and the verdict breakdown is unreadable without it."""


async def test_the_snapshot_carries_no_location_evidence(db: AsyncSession) -> None:
    """ADR-0005, as a property of the payload rather than a promise about the UI.

    A trail is planted so that there is something to leak. The business's
    interest ends at presence; the snapshot carries verdicts and their
    breakdowns, and not one coordinate, ping or accuracy figure.
    """
    assignment = await make_assignment(db)
    visit = await make_visit(db, assignment, state=VisitState.COMPLETED, ended_at=NOW)
    for offset_s in (0, 15, 30):
        db.add(
            Ping(
                visit_id=visit.id,
                lat=51.5080,
                lng=-0.1281,
                accuracy_m=10.0,
                reported_at=NOW + timedelta(seconds=offset_s),
                received_at=NOW + timedelta(seconds=offset_s),
                distance_m=0.0,
                classification=Classification.INSIDE,
            )
        )
    await db.commit()
    await make_verdict(db, visit)

    dumped = (await snapshot(db)).model_dump(mode="json")

    assert keys(dumped) & LOCATION_EVIDENCE == set()
    assert "pings" not in keys(dumped)
