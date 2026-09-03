"""The background sweeper: the three transitions nobody requests (spec.md §7).

`ABANDONED`, `UNREPORTED` and `EXPIRED` are the states this system reaches by
*nothing* happening. No participant asks for them and no HTTP request produces
one, which is why the dashboard is pushed rather than polled (ADR-0006) and why
this task exists at all.

**Single process by construction.** One `asyncio` task per process, no leader
election, no advisory lock, no `SELECT ... FOR UPDATE SKIP LOCKED` partitioning.
Two processes running this would both sweep, and the row locks below would keep
that *correct* — the loser's `FOR UPDATE` re-check finds a row already moved and
skips it — but it is not designed for and not tested. That limitation is
deliberate and shared with the SSE fan-out, recorded once for both (ADR-0006).
This paragraph exists so a reader does not take its absence for an oversight.

**A dead sweeper looks exactly like a quiet one.** Nothing observes these
transitions failing to happen: no client is waiting on a response, and a visit
that is never abandoned simply stays `ACTIVE` on a dashboard where staying
`ACTIVE` is normal. Everything below is arranged around that. Each sweep is its
own transaction, so one failing does not roll back the other two's work; each is
caught and logged and the pass continues; the pass itself is caught and logged
and the loop continues; and the task carries a done-callback, because the one
failure the loop cannot survive is the loop itself ending.
"""

import asyncio
import logging
from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fieldproof.config import ABANDON_AFTER_S, SWEEP_TICK_S
from fieldproof.events import Event, EventBus, transition_assignment, transition_visit
from fieldproof.schema import Assignment, Visit
from fieldproof.transitions import (
    NON_TERMINAL_VISIT_STATES,
    AssignmentState,
    DeadlinePassed,
    VisitEvent,
    VisitState,
)

log = logging.getLogger(__name__)


async def abandon_silent_visits(session: AsyncSession, *, now: datetime) -> list[Event]:
    """`ACTIVE` visits silent past `ABANDON_AFTER_S` → `ABANDONED` (spec.md §5, §7).

    **The row lock is issue 04's obligation being paid.** `ingest_ping` takes
    `SELECT ... FOR UPDATE OF visit` so that its state check and its INSERT are
    one decision; that only closes the race if whoever seals the visit takes the
    same lock. Reading a row here and writing it after an `await` — the shape
    this function has, because the transition runs in `transitions` and not in
    SQL — gets no lock for free. `with_for_update()` is what makes it safe.

    It also buys the second direction, which is the more valuable one. Under
    `READ COMMITTED` Postgres re-evaluates this `WHERE` against the row version
    it finds after waiting for the lock, so a visit that pinged microseconds
    before the cutoff — its `last_ping_at` written by a transaction that had not
    committed when this `SELECT` started — drops out of the result rather than
    being abandoned for silence it did not have. Waiting rather than
    `SKIP LOCKED` is the point: skipping would defer the row to the next tick,
    which is also correct, but it would not get the re-check.

    `ended_at` is stamped to `last_ping_at`, not to `now`. The alternatives are
    leaving it null, which forces every reader of a terminal visit to write
    `ended_at or last_ping_at`, or using the sweep's own clock, which credits the
    visit with the fifteen minutes of silence that killed it — and that number is
    the denominator of `dwell_ratio`. The last moment there was evidence is the
    honest end of the visit.
    """
    cutoff = now - timedelta(seconds=ABANDON_AFTER_S)
    visits = (
        (
            await session.execute(
                select(Visit)
                .where(Visit.state == VisitState.ACTIVE, Visit.last_ping_at < cutoff)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    events = []
    for visit in visits:
        events.extend(transition_visit(visit, VisitEvent.SILENCE_ELAPSED, at=now))
        visit.ended_at = visit.last_ping_at
    return events


async def expire_unreported_visits(session: AsyncSession, *, now: datetime) -> list[Event]:
    """`PENDING_REPORT` visits past `report_deadline_at` → `UNREPORTED` (spec.md §7).

    `report_deadline_at` is stamped when the visit is sealed (issue 08) and is
    null before that. SQL excludes nulls from `< now` on its own, and that is the
    behaviour wanted rather than an accident to guard against: a `PENDING_REPORT`
    visit with no deadline is a bug in whoever sealed it, and the failure mode of
    sweeping it would be taking away a participant's chance to write up work they
    already did. Leaving it `PENDING_REPORT` is visible on the dashboard and
    recoverable; `UNREPORTED` is terminal and is not.

    `ended_at` is already set here, by the end that produced `PENDING_REPORT`, so
    unlike the abandon sweep this one writes only the state.
    """
    visits = (
        (
            await session.execute(
                select(Visit)
                .where(
                    Visit.state == VisitState.PENDING_REPORT,
                    Visit.report_deadline_at < now,
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    events = []
    for visit in visits:
        events.extend(transition_visit(visit, VisitEvent.REPORT_DEADLINE_PASSED, at=now))
    return events


async def expire_overdue_assignments(session: AsyncSession, *, now: datetime) -> list[Event]:
    """`ASSIGNED` past `deadline_at`, with no visit in flight → `EXPIRED` (spec.md §5, §7).

    Locked for the same reason the visit sweeps are: `api.submit_report` moves
    an assignment to `FULFILLED` when a report lands, and that read-then-write
    races this one. `ingest_ping` deliberately scopes its lock `of=Visit` to
    stay off these rows, so nothing else in the request path contends with this
    scan.

    **A live visit stays this sweep.** `deadline_at` is a *start-by* time: a
    participant who opened a visit at 16:59 against a 17:00 deadline started in
    time, and keeps the ability to fulfil for as long as that visit is
    non-terminal. The assignment expires on the first pass after the visit
    reaches a terminal state without completing — `ABANDONED` by the sweep
    before this one, or `UNREPORTED` by the one in between — and never if it
    completes, because completion fulfils it (ADR-0004). Without this clause
    the visit ran to `PENDING_REPORT` normally and the report was then refused,
    since `advance_assignment` has no move out of `EXPIRED`; issue 05 found it,
    issue 08 decided it, and `test_an_assignment_stays_assigned_beneath_a_live_
    visit` pins it.

    The guard is a `NOT EXISTS` in the query rather than a branch in
    `advance_assignment`, because the machine is pure and cannot see other rows
    (`transitions`). It evaluates against this statement's snapshot, so the one
    window it does not close is a visit whose `open_visit` commits after this
    `SELECT` began: the width of one request, in the last instant before a
    pass, and recorded rather than fixed.
    """
    live_visit = select(Visit.id).where(
        Visit.assignment_id == Assignment.id,
        Visit.state.in_(NON_TERMINAL_VISIT_STATES),
    )
    assignments = (
        (
            await session.execute(
                select(Assignment)
                .where(
                    Assignment.state == AssignmentState.ASSIGNED,
                    Assignment.deadline_at < now,
                    ~exists(live_visit),
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    return [
        transition_assignment(assignment, DeadlinePassed(), at=now) for assignment in assignments
    ]


class Sweep(Protocol):
    """One sweep: rows in, rows moved, events out. A `Protocol` because the
    `now` keyword is keyword-only and `Callable[...]` cannot say so."""

    __name__: str

    def __call__(self, session: AsyncSession, *, now: datetime) -> Awaitable[list[Event]]: ...


SWEEPS: tuple[Sweep, ...] = (
    abandon_silent_visits,
    expire_unreported_visits,
    expire_overdue_assignments,
)
"""The three sweeps of one pass, in spec.md §7's order.

A tuple rather than three calls in `sweep_once`, so that the per-sweep
transaction and the per-sweep error handling are written once and a fourth sweep
cannot be added with either of them forgotten.
"""


async def sweep_once(
    factory: async_sessionmaker[AsyncSession], bus: EventBus, *, now: datetime
) -> None:
    """One pass: each sweep in its own transaction, each failure logged and survived.

    Three transactions rather than one. A single transaction would be tidier and
    would make a pass atomic, which sounds like a virtue and is not: these sweeps
    are independent, and one of them failing on a locked row or a dropped
    connection would roll back the work of the other two, so every tick would
    have to succeed at everything to accomplish anything.

    **Events are published after the commit, never before.** An event is a claim
    that a transition happened, and a publish before a commit that then fails is
    a dashboard showing a visit that is still `ACTIVE` in the database with no
    correction coming — the client re-snapshots on reconnect (ADR-0006) and would
    silently disagree with itself until then.

    `except Exception` does not catch `asyncio.CancelledError`, which derives
    from `BaseException`: shutdown cancels the task, and a sweeper that logged
    the cancellation and looped would hang it.
    """
    for sweep in SWEEPS:
        try:
            async with factory() as session:
                events = await sweep(session, now=now)
                await session.commit()
        except Exception:
            # The whole point of the sweeper surviving: a pass that cannot reach
            # the database is a pass that gets retried in SWEEP_TICK_S, not the
            # end of every future pass.
            log.exception("sweep %s failed; the pass continues", sweep.__name__)
            continue
        bus.publish(events)
        if events:
            log.info("sweep %s moved %d row(s)", sweep.__name__, len(events))


async def run_sweeper(
    factory: async_sessionmaker[AsyncSession],
    bus: EventBus,
    *,
    tick_s: float = SWEEP_TICK_S,
) -> None:
    """Sweep every `tick_s`, forever, until cancelled.

    Sweeps first and sleeps after, so a process that has been down catches up on
    startup rather than `SWEEP_TICK_S` later.

    The clock is read here, once per pass, and passed into the sweeps as a value.
    That is the opposite of `api.received_now`, which deliberately has no seam,
    and the two are not in tension: `received_at` is *evidence* and the claim
    the trust boundary makes is that nothing off the server can move it, whereas
    this `now` is a schedule. Nothing is stored from it that a participant could
    have influenced, and the alternative — tests that sleep fifteen real minutes
    to watch a visit be abandoned — is not a test of anything.
    """
    while True:
        try:
            await sweep_once(factory, bus, now=datetime.now(UTC))
        except Exception:
            # `sweep_once` already guards each sweep, so reaching here means the
            # loop's own scaffolding failed. Caught anyway: the alternative is
            # the task ending, and this is the failure that is invisible.
            log.exception("sweep pass failed; the sweeper continues")
        await asyncio.sleep(tick_s)


def start_sweeper(
    factory: async_sessionmaker[AsyncSession],
    bus: EventBus,
    *,
    tick_s: float = SWEEP_TICK_S,
) -> asyncio.Task[None]:
    """Run the sweeper as a background task, and make its death audible.

    `run_sweeper` never returns normally, so a completed task is always bad news
    — and a bare `create_task` swallows it: the exception sits on a task nobody
    awaits, and the process keeps serving requests with three transitions that
    silently no longer happen. The done-callback is the whole reason this
    function exists rather than being one line in the lifespan.
    """
    task = asyncio.create_task(run_sweeper(factory, bus, tick_s=tick_s), name="fieldproof-sweeper")
    task.add_done_callback(_report_death)
    return task


def _report_death(task: asyncio.Task[None]) -> None:
    """Log a sweeper that stopped. Cancellation is the one expected way out."""
    if task.cancelled():
        return
    log.critical(
        "the sweeper stopped; visits will no longer be abandoned, expired or marked unreported",
        exc_info=task.exception(),
    )


STOP_TIMEOUT_S = 5.0
"""How long shutdown waits for the sweeper to notice it was cancelled."""


async def stop_sweeper(task: asyncio.Task[None], *, timeout_s: float = STOP_TIMEOUT_S) -> None:
    """Cancel the sweeper and wait for it, so shutdown does not race the last pass.

    Bounded, and `asyncio.wait` rather than a bare `await task` or a `wait_for`
    — both of which wait for the cancellation to be *accepted*, and so hang
    forever on a task that swallows it. That is not hypothetical: widening
    either `except Exception` in this module to `BaseException` produces exactly
    such a task, and the symptom would be a server that never exits rather than
    a test that fails. A shutdown path with no upper bound is its own bug.

    Giving up is logged, not raised: the process is going down either way, and
    the only thing left to do about a sweeper that will not stop is to say so.
    """
    task.cancel()
    await asyncio.wait([task], timeout=timeout_s)
    if not task.done():
        log.error("the sweeper did not stop when cancelled; shutting down without it")
