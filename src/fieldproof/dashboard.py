"""The business dashboard: one snapshot, then the deltas (spec.md §6, ADR-0006).

Two things live here and the seam between them is the point. `snapshot` is a
query: the complete dashboard state, as `GET /api/dashboard` returns it. `stream`
is the protocol: subscribe to the bus, *then* take the snapshot, then hand over
every event as it arrives. That order is the whole correctness argument for
choosing SSE over polling, and it is the one thing in this module a reader
should check.

**Subscribe first, snapshot second.** Every publisher in this codebase commits
and then publishes (`sweeper.sweep_once` and every `api` handler), so a transition
that commits after the snapshot's `SELECT` publishes after it too — and a queue
registered before the `SELECT` is guaranteed to hold it. The other order has a
gap: a transition landing between the query and the subscription is in neither
the snapshot nor the stream, and nothing ever corrects it. The cost of the
right order is a transition that commits just *before* the `SELECT` and
publishes just after the subscription, which then arrives twice — once in the
snapshot and once as a delta. That is harmless by construction: a delta carries
the absolute `to_state`, not an increment, so applying it to a row already in
that state changes nothing. At-least-once with idempotent deltas is the
contract; the client is never asked to detect a gap.

**Reconnection is the same code path.** There is no `Last-Event-ID`, no replay
buffer and no event store, because a client that reconnects simply gets a fresh
snapshot from a fresh subscription. Do not add them (issue 06).

**What the business sees.** The snapshot carries verdicts and their breakdowns,
states, and timestamps — and no coordinates, no pings, no accuracy figures
(ADR-0005). The target location is deliberately absent too: nothing on the
dashboard renders it, and the one view that would (the map) is an internal audit
surface, not this one. A `COMPLETED` delta carries the same breakdown
(`events.VisitTransitioned.verdict`), so the client never re-snapshots to
render a report; the delta's `at` stands in for the snapshot's `computed_at`.
"""

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from datetime import datetime
from typing import assert_never, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fieldproof.config import SSE_KEEPALIVE_S
from fieldproof.events import AssignmentTransitioned, Event, EventBus, VisitTransitioned
from fieldproof.schema import Assignment, VerdictRecord, Visit
from fieldproof.transitions import AssignmentState, VisitState
from fieldproof.verification import Verdict

# ---------------------------------------------------------------- the snapshot


class DashboardVerdict(BaseModel):
    """A stored verdict with its full breakdown (spec.md §2, ADR-0005).

    Every column of `schema.VerdictRecord` except its ids: the dashboard renders
    the breakdown without recomputing anything, and `scoring_config_version` is
    what lets a business ask which rules produced a result.
    """

    model_config = ConfigDict(from_attributes=True)

    verdict: Verdict
    inside_s: float
    outside_s: float
    unattributed_s: float
    attributed_total_s: float
    dwell_ratio: float
    conclusive_pings: int
    total_pings: int
    visit_duration_s: float
    radius_m: float
    min_duration_s: int
    scoring_config_version: str
    computed_at: datetime


class DashboardVisit(BaseModel):
    """One attempt, as the business sees it: state, when, and the verdict if any.

    `verdict` is `None` for every visit that has not been reported on, which is
    every state but `COMPLETED` — and would also be a `COMPLETED` visit whose
    verdict row is missing, the state issue 08 calls "one the dashboard cannot
    render". This model renders it, as a completed visit with no verdict, rather
    than refusing the whole snapshot over one bad row.

    No `last_ping_at`: the ticking "last seen" view it would feed was cut
    (ADR-0006), and a field nothing renders is a field someone will render.
    """

    id: UUID
    state: VisitState
    started_at: datetime
    ended_at: datetime | None
    verdict: DashboardVerdict | None


class DashboardAssignment(BaseModel):
    """An assignment with every visit ever made against it, oldest first.

    The visits are nested rather than flattened because the attempt count is a
    signal in its own right (ADR-0001, issue 09), and `len(visits)` is that
    count with no second field to keep in step.
    """

    id: UUID
    business_name: str
    participant_name: str
    state: AssignmentState
    deadline_at: datetime
    radius_m: float
    min_duration_s: int
    visits: list[DashboardVisit]


class DashboardSnapshot(BaseModel):
    """The complete dashboard state. `GET /api/dashboard`, and the stream's first event."""

    assignments: list[DashboardAssignment]


type _JoinedRow = tuple[Assignment, Visit | None, VerdictRecord | None]


async def snapshot(session: AsyncSession) -> DashboardSnapshot:
    """The whole dashboard, from one statement.

    One `SELECT` and not three, because three would be three snapshots: under
    `READ COMMITTED` a report committing between a visits query and a verdicts
    query would attach a verdict to a visit this snapshot still shows as
    `PENDING_REPORT`. A single outer-joined statement sees one consistent
    instant, which is the property "snapshot" is supposed to name.

    Ordered by creation on both levels, with the id as a tiebreak, so that two
    snapshots of the same state serialise to the same bytes. `GET /api/dashboard`
    and the stream's first event are the same payload only if this is stable.
    """
    statement = (
        select(Assignment, Visit, VerdictRecord)
        .outerjoin(Visit, Visit.assignment_id == Assignment.id)
        .outerjoin(VerdictRecord, VerdictRecord.visit_id == Visit.id)
        .order_by(Assignment.created_at, Assignment.id, Visit.started_at, Visit.id)
    )
    # SQLAlchemy types an outer-joined entity as present; at runtime it is
    # `None` for an assignment with no visits. The cast is the boundary where
    # that gap is closed, so nothing below has to know it exists.
    rows = cast(Sequence[_JoinedRow], (await session.execute(statement)).tuples().all())

    assignments: dict[UUID, DashboardAssignment] = {}
    for assignment, visit, verdict in rows:
        view = assignments.get(assignment.id)
        if view is None:
            view = assignments[assignment.id] = DashboardAssignment(
                id=assignment.id,
                business_name=assignment.business_name,
                participant_name=assignment.participant_name,
                state=assignment.state,
                deadline_at=assignment.deadline_at,
                radius_m=assignment.radius_m,
                min_duration_s=assignment.min_duration_s,
                visits=[],
            )
        if visit is not None:
            view.visits.append(
                DashboardVisit(
                    id=visit.id,
                    state=visit.state,
                    started_at=visit.started_at,
                    ended_at=visit.ended_at,
                    verdict=None if verdict is None else DashboardVerdict.model_validate(verdict),
                )
            )
    return DashboardSnapshot(assignments=list(assignments.values()))


# ---------------------------------------------------------------- the stream


class Keepalive:
    """A frame that carries nothing. Sent when the bus has been quiet for a while.

    Its job is to make the write happen: a client that has vanished without
    closing its socket is only discovered when the server writes to it, and a
    stream that waits on a quiet bus never writes. See `config.SSE_KEEPALIVE_S`.
    """


KEEPALIVE = Keepalive()

type Frame = DashboardSnapshot | Event | Keepalive
"""Everything `stream` yields, in the order it yields them: one snapshot, then
events and keepalives for as long as the client stays."""


async def stream(
    bus: EventBus,
    take_snapshot: Callable[[], Awaitable[DashboardSnapshot]],
    *,
    keepalive_s: float = SSE_KEEPALIVE_S,
) -> AsyncGenerator[Frame, None]:
    """Subscribe, snapshot, then every event until the consumer goes away.

    The subscription is opened *before* `take_snapshot` runs — see the module
    docstring for why that order and not the other — and it is
    `EventBus.subscribe`'s context manager, so it closes on every way out of
    this generator: the consumer stopping, the consumer's task being cancelled
    while this waits on the queue, or the snapshot itself raising. There is no
    `finally` here to forget, which is how issue 06's leak is handled.

    `take_snapshot` is a callable rather than a session because the snapshot
    is the only moment the stream needs a database at all. The caller opens a
    session for exactly that long; a session held for the life of an SSE
    connection is a pooled connection held for the life of an SSE connection.
    """
    with bus.subscribe() as queue:
        yield await take_snapshot()
        while True:
            try:
                yield await asyncio.wait_for(queue.get(), timeout=keepalive_s)
            except TimeoutError:
                yield KEEPALIVE


# ---------------------------------------------------------------- the wire


_VISIT = TypeAdapter(VisitTransitioned)
_ASSIGNMENT = TypeAdapter(AssignmentTransitioned)


def encode(frame: Frame) -> bytes:
    """One frame as one Server-Sent Event.

    Named events — `snapshot`, `visit`, `assignment` — so the client registers a
    listener per kind rather than branching on a field inside the data. The
    `match` is exhaustive over `Event`: a variant added to the union and not
    here is a type error, which is the property issue 05 built the union for.

    The snapshot is serialised by the same call `GET /api/dashboard` uses, so
    the two are byte-identical by construction rather than by agreement.
    """
    match frame:
        case Keepalive():
            return b": keepalive\n\n"
        case DashboardSnapshot():
            return _event("snapshot", frame.model_dump_json().encode())
        case VisitTransitioned():
            return _event("visit", _VISIT.dump_json(frame))
        case AssignmentTransitioned():
            return _event("assignment", _ASSIGNMENT.dump_json(frame))
        case _:
            assert_never(frame)


def _event(name: str, data: bytes) -> bytes:
    """`event:` and `data:` lines, terminated by the blank line that ends an event.

    `data` is compact JSON and so contains no newline; if it ever did, the SSE
    rule is one `data:` line per line, which this does not implement because
    nothing here needs it.
    """
    return b"event: " + name.encode() + b"\ndata: " + data + b"\n\n"
