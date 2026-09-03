"""The HTTP layer, and the trust boundary that lives on it (spec.md §4, §6).

Everything a participant's browser can say arrives here, and nothing it says is
believed on its own terms. Three rules, and this module holds all three:

- **The server stamps `received_at`.** There is no seam for it — no injectable
  clock, no header, no field on the payload. `reported_at` is stored beside it
  and never scored on, so the two clocks can disagree as loudly as they like and
  only one of them is evidence.
- **State legality is `transitions`'.** The endpoints never ask whether a visit
  is `ACTIVE`. They run the move and answer 409 when the machine has no such
  move, so the sweeper and the API cannot drift apart on what is legal.
- **Geodesy and classification are `geo`'s and `verification`'s.** Ingest calls
  `TargetLocation.distance_m` and `classify`; it does not reimplement either.
  The stored `distance_m` is what makes verification an aggregate (ADR-0002),
  so a second implementation here would be a second answer to the question the
  verdict rests on.

The two status codes are not interchangeable, and issue 07's client depends on
which one it gets (see `Reason`).

The dashboard's two routes are the other side of the boundary: what the
*business* is shown, which is verdicts and never the trail (ADR-0005). Their
shape and the snapshot-then-stream protocol live in `dashboard`; this module
only puts them on the wire.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from enum import StrEnum
from http import HTTPStatus
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from fieldproof.config import BACKFILL_GRACE_S
from fieldproof.dashboard import DashboardSnapshot, encode, snapshot, stream
from fieldproof.database import create_engine, session_factory
from fieldproof.events import EventBus, transition_visit, visit_started
from fieldproof.schema import ONE_NON_TERMINAL_VISIT_INDEX, Assignment, Ping, Visit
from fieldproof.sweeper import start_sweeper, stop_sweeper
from fieldproof.transitions import (
    IllegalTransitionError,
    VisitEvent,
    start_visit,
)
from fieldproof.verification import classify


def received_now() -> datetime:
    """The server clock, at the moment a request is handled. Timezone-aware.

    Deliberately a plain function and not a FastAPI dependency. An overridable
    clock is the ordinary testable choice, and it is the wrong one *here*: the
    single claim this endpoint makes is that `received_at` came from the server
    and nothing on the wire can move it, and a seam that lets a caller supply it
    is a seam an integration test proves nothing through. The trust-boundary
    tests assert the relationship instead — `received_at` lands between the
    instant the test made the request and the instant it got the answer.
    """
    return datetime.now(UTC)


class Reason(StrEnum):
    """The machine-readable half of every error body.

    The client branches on this, not on prose. `illegal_transition` is the
    terminal one: spec.md §8 has the participant's page stop the interval,
    release the Wake Lock and offer a new visit. `ping_too_old` is not terminal
    and must never be confused with it — see `ingest_ping`.
    """

    NOT_FOUND = "not_found"
    ILLEGAL_TRANSITION = "illegal_transition"
    PING_TOO_OLD = "ping_too_old"


def _error(status: HTTPStatus, reason: Reason, message: str, **extra: Any) -> HTTPException:
    """An error whose body is `{reason, message, ...}`. Returned to be raised."""
    return HTTPException(
        status_code=status, detail={"reason": reason.value, "message": message, **extra}
    )


class PingRequest(BaseModel):
    """One reported position, as the browser sends it (spec.md §8).

    Every field is constrained, because every field is client-supplied and two
    of them can change a verdict if they are not. `accuracy_m` is the sharp one:
    `classify` reads it as a half-width, so a negative value *shrinks* the
    uncertainty interval and turns pings that should be inconclusive into
    conclusive ones in whichever direction suits the sender. Accuracy describes
    uncertainty and never trustworthiness (CONTEXT.md) — but that only holds
    while it is a real accuracy.

    `reported_at` is `AwareDatetime`, so a naive client timestamp is a 422 rather
    than a datetime this codebase would have to guess a zone for. `extra` is
    forbidden: the browser sends what this model names, and a field we silently
    dropped would look accepted.
    """

    model_config = ConfigDict(extra="forbid")

    lat: float = Field(ge=-90.0, le=90.0)
    lng: float = Field(ge=-180.0, le=180.0)
    accuracy_m: float = Field(ge=0.0)
    reported_at: AwareDatetime


class PingAccepted(BaseModel):
    """202. Carries the server's clock and nothing about judgement.

    Not the classification: the participant's browser learning `INSIDE` or
    `OUTSIDE` per ping would turn a ping trail into a game of "walk until it
    says inside", and there is no participant-facing reason to know. Verdicts
    are computed once, on a sealed trail (ADR-0002).
    """

    received_at: datetime


class VisitStarted(BaseModel):
    """201. The id the participant's page pings against for the rest of the visit."""

    visit_id: UUID
    started_at: datetime


async def _session(request: Request) -> AsyncIterator[AsyncSession]:
    """One session per request, rolled back unless the handler commits.

    The handler owning the commit is what makes the row lock in `ingest_ping`
    mean anything: the lock is held for the life of this transaction, so a
    session that committed early would drop it before the ping was written.
    """
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        yield session


Session = Annotated[AsyncSession, Depends(_session)]


def _bus(request: Request) -> EventBus:
    """The one bus for this app. The sweeper publishes to this same object."""
    bus: EventBus = request.app.state.bus
    return bus


Bus = Annotated[EventBus, Depends(_bus)]


async def _illegal_transition(_: Request, exc: Exception) -> JSONResponse:
    """Every `IllegalTransitionError` becomes a 409, in one place.

    `transitions` says "the API layer answers these with 409"; this is the
    sentence made true rather than repeated per route. It is also why a handler
    can call `advance_visit` and simply use the result: the rejection path needs
    no `try` at the call site, so there is no call site that can forget one and
    return a 500 instead.
    """
    return JSONResponse(
        status_code=HTTPStatus.CONFLICT,
        content={"reason": Reason.ILLEGAL_TRANSITION.value, "message": str(exc)},
    )


def create_app(*, engine: AsyncEngine | None = None) -> FastAPI:
    """The application.

    An `engine` passed in is the caller's to dispose, and the routes are wired
    the moment this returns — which is what lets the tests drive them through
    `ASGITransport` with no lifespan manager. With no engine, the app makes and
    disposes its own inside the lifespan, because an engine binds to the loop
    that will use it and there is no such loop at import time.

    The **sweeper** starts in the lifespan either way, and only there: it needs a
    running loop, and a test that got one for free with every `create_app` would
    have three sweeps running against its fixtures. `tests/test_sweeper.py`
    drives the lifespan explicitly to check that a served app does start it —
    never starting the sweeper is the loudest version of spec.md §7's failure
    mode and the one no request would ever reveal.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = create_engine() if engine is None else None
        if owned is not None:
            app.state.session_factory = session_factory(owned)
        app.state.sweeper = start_sweeper(app.state.session_factory, app.state.bus)
        try:
            yield
        finally:
            await stop_sweeper(app.state.sweeper)
            if owned is not None:
                await owned.dispose()

    app = FastAPI(title="fieldproof", lifespan=lifespan)
    app.state.bus = EventBus()
    if engine is not None:
        app.state.session_factory = session_factory(engine)
    app.add_exception_handler(IllegalTransitionError, _illegal_transition)

    @app.post(
        "/api/assignments/{assignment_id}/visits",
        status_code=HTTPStatus.CREATED,
        response_model=VisitStarted,
    )
    async def open_visit(assignment_id: UUID, session: Session, bus: Bus) -> VisitStarted:
        """Start a visit against an assignment (spec.md §5, §6). 409 if it may not.

        Publishes on the same bus as the sweeper, after the commit — a new visit
        is the one transition the dashboard learns about from a request rather
        than a sweep, and a consumer receiving it has no way to tell (`events`).

        Two referees, one answer. `start_visit` reads the assignment's state and
        the latest visit's and refuses what it can see; the partial unique index
        refuses what `start_visit` cannot see, which is a second request that
        read the same rows in the same instant. The loser of that race is
        translated back into the machine's own error, so the body it receives is
        the body the first check would have produced. A client cannot tell which
        referee refused it, and nothing about ADR-0001 depends on it knowing.
        """
        started_at = received_now()
        assignment = await session.get(Assignment, assignment_id)
        if assignment is None:
            raise _error(HTTPStatus.NOT_FOUND, Reason.NOT_FOUND, f"no assignment {assignment_id}")

        # The latest visit, by the machine's definition of latest. Ties are
        # possible in principle and harmless: `start_visit` only distinguishes
        # non-terminal from terminal, and a tie it resolved the wrong way is
        # exactly the case the index below catches.
        latest_visit = (
            await session.execute(
                select(Visit.state)
                .where(Visit.assignment_id == assignment_id)
                .order_by(Visit.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        visit = Visit(
            assignment_id=assignment_id,
            state=start_visit(assignment=assignment.state, latest_visit=latest_visit),
            started_at=started_at,
            # spec.md §5 has the sweeper abandon on silence, and silence starts
            # now: a visit that never pings is `ABANDONED` 15 minutes after it
            # opened, not never (`Visit.last_ping_at`).
            last_ping_at=started_at,
            created_at=started_at,
        )
        session.add(visit)
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            if _violated_index(exc) != ONE_NON_TERMINAL_VISIT_INDEX:
                raise
            raise IllegalTransitionError(
                "this assignment already has a non-terminal visit"
            ) from exc
        bus.publish([visit_started(visit)])
        return VisitStarted(visit_id=visit.id, started_at=started_at)

    @app.post(
        "/api/visits/{visit_id}/pings",
        status_code=HTTPStatus.ACCEPTED,
        response_model=PingAccepted,
    )
    async def ingest_ping(
        visit_id: UUID, ping: PingRequest, session: Session, bus: Bus
    ) -> PingAccepted:
        """Record one ping (spec.md §4). INSERT-only; 409 if the visit is not ACTIVE.

        **The row lock is the point of this handler.** `advance_visit` reads a
        state and the INSERT writes against it, and between those two an `await`
        gives the loop to whoever else is holding a decision about this visit —
        the sweeper abandoning it on silence (§7), or `POST /end` sealing it
        (§5). Under `READ COMMITTED` an unlocked read is a snapshot, so that
        commit is invisible here and the ping lands on a visit that is already
        terminal: a trail that was sealed and then grew. `verify` states the
        invariant this breaks — every ping inside the visit window — and the
        damage is a real number on the dashboard, `unattributed_s` going
        negative because attributed time now exceeds the visit's duration.

        `FOR UPDATE` closes it in both directions, and the second is a bonus:
        the sealer must wait for this transaction, and Postgres then re-checks
        its own `WHERE last_ping_at < cutoff` against the row we just wrote, so
        a visit that pinged microseconds before the cutoff is not abandoned for
        silence. `of=Visit` keeps the lock off the assignment row, which the
        sweeper's `EXPIRED` scan is meanwhile writing to for unrelated reasons.

        What the lock is *not* for: `last_ping_at`. Two pings racing on that
        column are last-writer-wins over a value the sweeper compares against a
        900-second window, and the loser is out by the width of one request.
        """
        received_at = received_now()

        # One round trip, and it takes the lock: the assignment is joined rather
        # than fetched afterwards so the locked section holds no second await.
        row = (
            (
                await session.execute(
                    select(Visit, Assignment)
                    .join(Assignment, Visit.assignment_id == Assignment.id)
                    .where(Visit.id == visit_id)
                    .with_for_update(of=Visit)
                )
            )
            .tuples()
            .one_or_none()
        )
        if row is None:
            raise _error(HTTPStatus.NOT_FOUND, Reason.NOT_FOUND, f"no visit {visit_id}")
        visit, assignment = row

        # State first, staleness second, and the order is a decision. Both can
        # hold at once — an iOS tab resuming after a lock posts a cached fix at
        # a visit the sweeper abandoned while it slept — and the two answers ask
        # opposite things of the client: 409 stops it for good, 422 drops one
        # reading and keeps the interval running. The closed visit is the more
        # fundamental fact (no payload could have succeeded), so it wins, and
        # the participant is told a cycle earlier than they would be otherwise.
        #
        # Through `transition_visit` rather than `advance_visit` directly, so
        # that this handler and the sweeper move a visit by the same call — and
        # the events it produces are published like anyone else's, without this
        # handler knowing that a ping's list is always empty. Knowing it here is
        # what would let the rule be deleted over there without a test noticing
        # (`events.transition_visit`).
        events = transition_visit(visit, VisitEvent.PING, at=received_at)

        age_s = (received_at - ping.reported_at).total_seconds()
        if age_s > BACKFILL_GRACE_S:
            raise _error(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                Reason.PING_TOO_OLD,
                f"reported_at is {age_s:.0f}s old, past the {BACKFILL_GRACE_S}s backfill grace",
                age_s=age_s,
                grace_s=BACKFILL_GRACE_S,
            )
        # Only the past is rejected. A `reported_at` in the future is skew like
        # any other, and spec.md §4 keeps the column as a tamper signal rather
        # than a gate: nothing is scored on it, so storing the lie is strictly
        # more useful than refusing it.

        target = assignment.target
        distance_m = target.distance_m(lat=ping.lat, lng=ping.lng)
        session.add(
            Ping(
                visit_id=visit.id,
                lat=ping.lat,
                lng=ping.lng,
                accuracy_m=ping.accuracy_m,
                reported_at=ping.reported_at,
                received_at=received_at,
                distance_m=distance_m,
                # Stored for the dashboard and for auditing the radius of the
                # day; `verify` re-derives its own (ADR-0002, `schema.Ping`).
                classification=classify(
                    distance_m=distance_m,
                    accuracy_m=ping.accuracy_m,
                    radius_m=target.radius_m,
                ),
            )
        )
        visit.last_ping_at = received_at
        await session.commit()
        bus.publish(events)
        return PingAccepted(received_at=received_at)

    @app.get("/api/dashboard", response_model=DashboardSnapshot)
    async def dashboard(session: Session) -> Response:
        """The complete dashboard state (spec.md §6). The stream's first event, as JSON.

        Returns a `Response` built from `model_dump_json` rather than the model
        itself, so that this body and the stream's `snapshot` event come out of
        the same serialiser. FastAPI's own path would agree today; this makes
        agreeing not a thing that can drift.
        """
        return Response(
            content=(await snapshot(session)).model_dump_json(),
            media_type="application/json",
        )

    @app.get("/api/dashboard/stream")
    async def dashboard_stream(request: Request, bus: Bus) -> StreamingResponse:
        """Snapshot, then deltas, for as long as the client stays (ADR-0006).

        **No `Session` dependency, on purpose.** A request-scoped session lives
        as long as the response does, and this response lives as long as the
        browser tab: that is one pooled connection per open dashboard, held to
        serve one query. The stream opens its own session for exactly the
        snapshot and closes it before the first event is written.

        Disconnection is handled by Starlette: when the socket closes it cancels
        the task iterating this generator, the cancellation lands inside
        `dashboard.stream` at its wait on the queue, and the subscription's
        context manager unregisters the queue. A socket that goes silent
        without closing is caught by the keepalive instead
        (`config.SSE_KEEPALIVE_S`): the next write fails, with the same result.

        `Cache-Control: no-cache` because a cached event stream is a stale
        dashboard that never updates; `X-Accel-Buffering: no` so an nginx in
        front does not hold the snapshot back until the buffer fills.

        **An open stream holds up shutdown, and only the server's config can
        bound that.** On SIGTERM uvicorn stops accepting, then waits for every
        in-flight response to finish before it runs the lifespan's shutdown —
        and a stream finishes when the browser tab closes, not before. Its
        default wait is unbounded (measured: a server with one dashboard open
        was still up 10s after SIGTERM; with the flag, down in 2.2s). Nothing
        in the application can shorten it, because the lifespan hook that
        could tell streams to stop runs *after* the wait. Serve with
        `--timeout-graceful-shutdown <s>`; see `app` below.
        """
        factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory

        async def take_snapshot() -> DashboardSnapshot:
            async with factory() as session:
                return await snapshot(session)

        return StreamingResponse(
            (encode(frame) async for frame in stream(bus, take_snapshot)),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


def _violated_index(exc: IntegrityError) -> str | None:
    """The name of the constraint Postgres refused on, if it named one.

    SQLAlchemy wraps the driver's exception twice, and only the innermost one —
    asyncpg's — carries `constraint_name`. Reading it beats matching the message
    text: this is the difference between recognising *our* index and recognising
    any sentence containing its name, and the caller re-raises everything else
    rather than answering 409 to an integrity error it did not understand.
    """
    return getattr(getattr(exc.orig, "__cause__", None), "constraint_name", None)


app = create_app()
"""The application uvicorn serves. Owns its own engine.

    uvicorn fieldproof.api:app --timeout-graceful-shutdown 5

The flag is not optional once a dashboard is open: without it, uvicorn's
shutdown waits for the SSE stream to end, which is when the tab closes
(`dashboard_stream`). Five seconds is long enough for any request that is not
a stream to finish.
"""
