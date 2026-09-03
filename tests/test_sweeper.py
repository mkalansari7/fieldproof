"""The sweeper (issue 05, spec.md §7) and the bus it shares with the API.

Two things are under test and they are not the same thing. The **sweeps** are
row selection and state changes, tested by planting rows at chosen ages and
running one pass — `now` is a parameter here precisely so that watching a visit
be abandoned does not take fifteen real minutes. The **loop** is the part that
has to survive, tested by breaking it on purpose: a sweep that raises, a pass
that raises, a commit that fails, a cancellation. A dead sweeper looks exactly
like a quiet one, so every one of those has an assertion that it kept going.

The row-lock tests are the second half of issue 04's obligation. `ingest_ping`
locks the visit so its state check and its INSERT are one decision; that is only
closed if the sweeper takes the same lock, and `test_a_visit_that_pinged_as_the
_sweep_ran_is_not_abandoned` is where that is proved rather than asserted in a
docstring.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fieldproof import sweeper
from fieldproof.api import create_app
from fieldproof.config import ABANDON_AFTER_S
from fieldproof.database import create_engine
from fieldproof.events import (
    AssignmentTransitioned,
    Event,
    EventBus,
    VisitTransitioned,
    transition_visit,
)
from fieldproof.schema import Assignment, Visit, new_assignment
from fieldproof.sweeper import (
    abandon_silent_visits,
    expire_overdue_assignments,
    expire_unreported_visits,
    start_sweeper,
    stop_sweeper,
    sweep_once,
)
from fieldproof.transitions import (
    NON_TERMINAL_VISIT_STATES,
    AssignmentState,
    VisitEvent,
    VisitState,
)
from fieldproof.verification import Verdict, Verification
from tests.conftest import TEST_DATABASE_URL

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
"""The pass's clock. Fixed, because every case here is about an *interval* — how
long a visit has been silent, how far past a deadline — and a fixed `now` makes
each age a subtraction a reader can check rather than a tolerance."""

NOT_ACTIVE = sorted(set(VisitState) - {VisitState.ACTIVE}, key=lambda state: state.value)
NOT_PENDING_REPORT = sorted(
    set(VisitState) - {VisitState.PENDING_REPORT}, key=lambda state: state.value
)
NOT_ASSIGNED = sorted(
    set(AssignmentState) - {AssignmentState.ASSIGNED}, key=lambda state: state.value
)
NON_TERMINAL = sorted(NON_TERMINAL_VISIT_STATES, key=lambda state: state.value)
TERMINAL = sorted(set(VisitState) - NON_TERMINAL_VISIT_STATES, key=lambda state: state.value)


async def make_assignment(
    db: AsyncSession,
    *,
    state: AssignmentState = AssignmentState.ASSIGNED,
    overdue_by_s: float = -3600.0,
) -> Assignment:
    """A committed assignment whose `deadline_at` is `overdue_by_s` before `NOW`.

    Negative by default, so the assignment is *not* overdue unless a test says
    so: the sweeps under test here mostly want one fixture moving at a time.
    """
    assignment = new_assignment(
        business_name="Northwind Coffee",
        participant_name="Sam Okonjo",
        target_lat=51.5080,
        target_lng=-0.1281,
        deadline_at=NOW - timedelta(seconds=overdue_by_s),
        created_at=NOW - timedelta(days=1),
    )
    assignment.state = state
    db.add(assignment)
    await db.commit()
    return assignment


async def make_visit(
    db: AsyncSession,
    assignment: Assignment,
    *,
    state: VisitState = VisitState.ACTIVE,
    silent_for_s: float = 0.0,
    report_overdue_by_s: float | None = None,
) -> Visit:
    """A committed visit, `silent_for_s` since its last ping as of `NOW`.

    `report_overdue_by_s` sets `report_deadline_at`; left `None` the column stays
    null, which is what a visit that has not been sealed looks like.
    """
    started_at = NOW - timedelta(seconds=silent_for_s + 60)
    visit = Visit(
        assignment_id=assignment.id,
        state=state,
        started_at=started_at,
        ended_at=None,
        last_ping_at=NOW - timedelta(seconds=silent_for_s),
        report_deadline_at=(
            None if report_overdue_by_s is None else NOW - timedelta(seconds=report_overdue_by_s)
        ),
        created_at=started_at,
    )
    db.add(visit)
    await db.commit()
    return visit


async def reload_visit(db: AsyncSession, visit: Visit) -> Visit:
    """Re-read the row the sweeper wrote.

    A refresh and not an attribute read: the sweeper committed on a different
    connection, and `session_factory` sets `expire_on_commit=False`, so `visit`
    still holds whatever this session last saw.
    """
    await db.refresh(visit)
    return visit


async def reload_assignment(db: AsyncSession, assignment: Assignment) -> Assignment:
    await db.refresh(assignment)
    return assignment


async def eventually(check: Callable[[], Awaitable[bool]], *, timeout_s: float = 3.0) -> None:
    """Poll `check` until it holds. Fails the test rather than hanging the run.

    The loop tests are the only ones that wait on real time, and they wait on a
    condition rather than a duration: a `sleep` long enough to be reliable on a
    loaded machine is a `sleep` that makes the suite slow on every other one.
    """
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        if await check():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"condition never held within {timeout_s}s")


# ---------------------------------------------------------------- abandon: ACTIVE -> ABANDONED


async def test_a_silent_visit_is_abandoned(
    db: AsyncSession, factory: async_sessionmaker[AsyncSession]
) -> None:
    assignment = await make_assignment(db)
    visit = await make_visit(db, assignment, silent_for_s=ABANDON_AFTER_S + 1)

    async with factory() as session:
        events = await abandon_silent_visits(session, now=NOW)
        await session.commit()

    assert (await reload_visit(db, visit)).state is VisitState.ABANDONED
    assert events == [
        VisitTransitioned(
            visit_id=visit.id,
            assignment_id=assignment.id,
            from_state=VisitState.ACTIVE,
            to_state=VisitState.ABANDONED,
            at=NOW,
        )
    ]


async def test_an_abandoned_visit_ends_at_its_last_ping(
    db: AsyncSession, factory: async_sessionmaker[AsyncSession]
) -> None:
    """`ended_at` is the last moment there was evidence, not the moment of the sweep.

    The sweep runs up to `SWEEP_TICK_S` after the visit actually died and there
    is nothing between the two but silence. Stamping `now` would credit the
    visit with fifteen minutes of duration it spent producing nothing, and that
    number is `verify`'s denominator.
    """
    assignment = await make_assignment(db)
    visit = await make_visit(db, assignment, silent_for_s=ABANDON_AFTER_S + 1)

    async with factory() as session:
        await abandon_silent_visits(session, now=NOW)
        await session.commit()

    swept = await reload_visit(db, visit)
    assert swept.ended_at == swept.last_ping_at
    assert swept.ended_at != NOW


@pytest.mark.parametrize(
    ("silent_for_s", "expected"),
    [
        (ABANDON_AFTER_S - 1, VisitState.ACTIVE),
        (ABANDON_AFTER_S, VisitState.ACTIVE),
        (ABANDON_AFTER_S + 1, VisitState.ABANDONED),
    ],
    ids=["under", "exactly", "over"],
)
async def test_the_abandon_window_is_exclusive_at_its_edge(
    db: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    silent_for_s: float,
    expected: VisitState,
) -> None:
    """Silence *past* `ABANDON_AFTER_S` (spec.md §5), so exactly at it survives.

    Testable to the second here, unlike the ingest grace boundary, because this
    clock is a parameter and not a trust boundary (`sweeper.run_sweeper`).
    """
    assignment = await make_assignment(db)
    visit = await make_visit(db, assignment, silent_for_s=silent_for_s)

    async with factory() as session:
        await abandon_silent_visits(session, now=NOW)
        await session.commit()

    assert (await reload_visit(db, visit)).state is expected


@pytest.mark.parametrize("state", NOT_ACTIVE, ids=lambda state: state.value)
async def test_only_active_visits_are_abandoned(
    db: AsyncSession, factory: async_sessionmaker[AsyncSession], state: VisitState
) -> None:
    """Long silence is not a reason to touch a visit that is not `ACTIVE`.

    Parametrized over `set(VisitState) - {ACTIVE}` rather than four hand-written
    states: a sixth visit state becomes a case here without anyone adding one.
    `PENDING_REPORT` is the one that matters — it is silent by definition, and a
    sweep keyed on silence alone would abandon every sealed visit awaiting a
    write-up.
    """
    assignment = await make_assignment(db)
    visit = await make_visit(db, assignment, state=state, silent_for_s=ABANDON_AFTER_S * 10)

    async with factory() as session:
        events = await abandon_silent_visits(session, now=NOW)
        await session.commit()

    assert (await reload_visit(db, visit)).state is state
    assert events == []


# ------------------------------------------------- unreported: PENDING_REPORT -> UNREPORTED


async def test_an_overdue_pending_report_visit_becomes_unreported(
    db: AsyncSession, factory: async_sessionmaker[AsyncSession]
) -> None:
    assignment = await make_assignment(db)
    visit = await make_visit(
        db, assignment, state=VisitState.PENDING_REPORT, report_overdue_by_s=1.0
    )

    async with factory() as session:
        events = await expire_unreported_visits(session, now=NOW)
        await session.commit()

    assert (await reload_visit(db, visit)).state is VisitState.UNREPORTED
    assert [event.to_state for event in events] == [VisitState.UNREPORTED]


@pytest.mark.parametrize(
    ("report_overdue_by_s", "expected"),
    [
        (-1.0, VisitState.PENDING_REPORT),
        (0.0, VisitState.PENDING_REPORT),
        (1.0, VisitState.UNREPORTED),
    ],
    ids=["before", "exactly", "after"],
)
async def test_the_report_deadline_is_exclusive_at_its_edge(
    db: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    report_overdue_by_s: float,
    expected: VisitState,
) -> None:
    """`past report_deadline_at` (spec.md §7), so the deadline second itself is not past it.

    The same strictness as the abandon window, tested the same way, because the
    two predicates are written in two places and nothing but a test keeps them
    agreeing about which side of the boundary is inclusive.
    """
    assignment = await make_assignment(db)
    visit = await make_visit(
        db,
        assignment,
        state=VisitState.PENDING_REPORT,
        report_overdue_by_s=report_overdue_by_s,
    )

    async with factory() as session:
        await expire_unreported_visits(session, now=NOW)
        await session.commit()

    assert (await reload_visit(db, visit)).state is expected


async def test_a_pending_report_visit_inside_its_window_is_left_alone(
    db: AsyncSession, factory: async_sessionmaker[AsyncSession]
) -> None:
    assignment = await make_assignment(db)
    visit = await make_visit(
        db, assignment, state=VisitState.PENDING_REPORT, report_overdue_by_s=-1.0
    )

    async with factory() as session:
        await expire_unreported_visits(session, now=NOW)
        await session.commit()

    assert (await reload_visit(db, visit)).state is VisitState.PENDING_REPORT


async def test_a_pending_report_visit_with_no_deadline_is_left_alone(
    db: AsyncSession, factory: async_sessionmaker[AsyncSession]
) -> None:
    """A null `report_deadline_at` is someone else's bug, and `UNREPORTED` is terminal.

    SQL drops nulls from `< now` on its own; this test is here to say that is
    the wanted behaviour and not an accident. Sweeping the row would take away a
    participant's chance to write up work they already did, and the state it
    would put them in is unrecoverable. Leaving it visible on the dashboard is
    the recoverable failure.
    """
    assignment = await make_assignment(db)
    visit = await make_visit(db, assignment, state=VisitState.PENDING_REPORT)
    assert visit.report_deadline_at is None

    async with factory() as session:
        events = await expire_unreported_visits(session, now=NOW)
        await session.commit()

    assert (await reload_visit(db, visit)).state is VisitState.PENDING_REPORT
    assert events == []


@pytest.mark.parametrize("state", NOT_PENDING_REPORT, ids=lambda state: state.value)
async def test_only_pending_report_visits_are_swept_for_the_report_deadline(
    db: AsyncSession, factory: async_sessionmaker[AsyncSession], state: VisitState
) -> None:
    assignment = await make_assignment(db)
    visit = await make_visit(db, assignment, state=state, report_overdue_by_s=10_000.0)

    async with factory() as session:
        events = await expire_unreported_visits(session, now=NOW)
        await session.commit()

    assert (await reload_visit(db, visit)).state is state
    assert events == []


# ---------------------------------------------------------- expire: ASSIGNED -> EXPIRED


async def test_an_overdue_assignment_expires(
    db: AsyncSession, factory: async_sessionmaker[AsyncSession]
) -> None:
    assignment = await make_assignment(db, overdue_by_s=1.0)

    async with factory() as session:
        events = await expire_overdue_assignments(session, now=NOW)
        await session.commit()

    assert (await reload_assignment(db, assignment)).state is AssignmentState.EXPIRED
    assert events == [
        AssignmentTransitioned(
            assignment_id=assignment.id,
            from_state=AssignmentState.ASSIGNED,
            to_state=AssignmentState.EXPIRED,
            at=NOW,
        )
    ]


@pytest.mark.parametrize(
    ("overdue_by_s", "expected"),
    [
        (-1.0, AssignmentState.ASSIGNED),
        (0.0, AssignmentState.ASSIGNED),
        (1.0, AssignmentState.EXPIRED),
    ],
    ids=["before", "exactly", "after"],
)
async def test_the_assignment_deadline_is_exclusive_at_its_edge(
    db: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    overdue_by_s: float,
    expected: AssignmentState,
) -> None:
    """The third boundary, strict like the other two.

    An assignment due at 17:00 is still live at 17:00. Worth its own case rather
    than trusting the pattern: this is the only one of the three deadlines a
    business chose, so it is the one someone will be looking at a clock about.
    """
    assignment = await make_assignment(db, overdue_by_s=overdue_by_s)

    async with factory() as session:
        await expire_overdue_assignments(session, now=NOW)
        await session.commit()

    assert (await reload_assignment(db, assignment)).state is expected


@pytest.mark.parametrize("state", NOT_ASSIGNED, ids=lambda state: state.value)
async def test_only_assigned_assignments_expire(
    db: AsyncSession, factory: async_sessionmaker[AsyncSession], state: AssignmentState
) -> None:
    """`EXPIRED` and `FULFILLED` are terminal, and a passed deadline does not reopen them.

    `FULFILLED` is the case with teeth: an assignment someone completed on time
    must not turn `EXPIRED` the moment its deadline goes by.
    """
    assignment = await make_assignment(db, state=state, overdue_by_s=10_000.0)

    async with factory() as session:
        events = await expire_overdue_assignments(session, now=NOW)
        await session.commit()

    assert (await reload_assignment(db, assignment)).state is state
    assert events == []


@pytest.mark.parametrize("state", NON_TERMINAL, ids=lambda state: state.value)
async def test_an_assignment_stays_assigned_beneath_a_live_visit(
    db: AsyncSession, factory: async_sessionmaker[AsyncSession], state: VisitState
) -> None:
    """`deadline_at` means *start by* (spec.md §5, §7; decided in issue 08).

    A participant standing in the shop at 16:59 against a 17:00 deadline
    started in time. Issue 05 found that the rule as first written expired the
    assignment beneath them, and `advance_assignment` has no move out of
    `EXPIRED`, so the report they filed could not fulfil it. The sweep now
    skips an assignment with a non-terminal visit, for both non-terminal
    states: a visit already sealed and awaiting its write-up is as live, for
    this purpose, as one still pinging.
    """
    assignment = await make_assignment(db, overdue_by_s=1.0)
    visit = await make_visit(db, assignment, state=state)

    async with factory() as session:
        events = await expire_overdue_assignments(session, now=NOW)
        await session.commit()

    assert (await reload_assignment(db, assignment)).state is AssignmentState.ASSIGNED
    assert (await reload_visit(db, visit)).state is state
    assert events == []


@pytest.mark.parametrize("state", TERMINAL, ids=lambda state: state.value)
async def test_an_assignment_expires_once_its_visit_has_ended_without_fulfilling(
    db: AsyncSession, factory: async_sessionmaker[AsyncSession], state: VisitState
) -> None:
    """The other half of "start by": a terminal visit is no longer a reason to wait.

    `ABANDONED` and `UNREPORTED` are the real cases — the attempt died, the
    deadline is past, and a new attempt may not start (`start_visit` refuses
    `EXPIRED`). `COMPLETED` under a still-`ASSIGNED` assignment is a row some
    transaction got wrong, since completion fulfils in the same stroke; the
    sweep treats it like any other terminal visit rather than guessing.
    """
    assignment = await make_assignment(db, overdue_by_s=1.0)
    await make_visit(db, assignment, state=state)

    async with factory() as session:
        events = await expire_overdue_assignments(session, now=NOW)
        await session.commit()

    assert (await reload_assignment(db, assignment)).state is AssignmentState.EXPIRED
    assert [event.to_state for event in events] == [AssignmentState.EXPIRED]


async def test_a_visit_abandoned_in_a_pass_lets_its_assignment_expire_in_the_same_pass(
    db: AsyncSession, factory: async_sessionmaker[AsyncSession], bus: EventBus
) -> None:
    """spec.md §7's order, with a consequence: abandon runs before expire.

    Each sweep is its own transaction and commits before the next begins, so
    the expiry sweep's `NOT EXISTS` sees the visit the abandon sweep just
    closed. An overdue assignment whose last attempt went silent is `EXPIRED`
    one pass later, not two.
    """
    assignment = await make_assignment(db, overdue_by_s=1.0)
    visit = await make_visit(db, assignment, silent_for_s=ABANDON_AFTER_S + 1)

    with bus.subscribe() as queue:
        await sweep_once(factory, bus, now=NOW)

    assert (await reload_visit(db, visit)).state is VisitState.ABANDONED
    assert (await reload_assignment(db, assignment)).state is AssignmentState.EXPIRED
    assert [type(event) for event in drain(queue)] == [VisitTransitioned, AssignmentTransitioned]


# ---------------------------------------------------------------- one pass


async def test_one_pass_runs_all_three_sweeps(
    db: AsyncSession, factory: async_sessionmaker[AsyncSession], bus: EventBus
) -> None:
    """The three are independent and a pass does all of them.

    They are three transactions, so this also asserts the thing the split was
    for: no sweep's work is conditional on another's.
    """
    silent = await make_assignment(db)
    silent_visit = await make_visit(db, silent, silent_for_s=ABANDON_AFTER_S + 1)

    unwritten = await make_assignment(db)
    unwritten_visit = await make_visit(
        db, unwritten, state=VisitState.PENDING_REPORT, report_overdue_by_s=1.0
    )

    overdue = await make_assignment(db, overdue_by_s=1.0)

    with bus.subscribe() as queue:
        await sweep_once(factory, bus, now=NOW)

    assert (await reload_visit(db, silent_visit)).state is VisitState.ABANDONED
    assert (await reload_visit(db, unwritten_visit)).state is VisitState.UNREPORTED
    assert (await reload_assignment(db, overdue)).state is AssignmentState.EXPIRED
    assert drain(queue) == [
        VisitTransitioned(
            visit_id=silent_visit.id,
            assignment_id=silent.id,
            from_state=VisitState.ACTIVE,
            to_state=VisitState.ABANDONED,
            at=NOW,
        ),
        VisitTransitioned(
            visit_id=unwritten_visit.id,
            assignment_id=unwritten.id,
            from_state=VisitState.PENDING_REPORT,
            to_state=VisitState.UNREPORTED,
            at=NOW,
        ),
        AssignmentTransitioned(
            assignment_id=overdue.id,
            from_state=AssignmentState.ASSIGNED,
            to_state=AssignmentState.EXPIRED,
            at=NOW,
        ),
    ]


async def test_an_idle_pass_publishes_nothing(
    db: AsyncSession, factory: async_sessionmaker[AsyncSession], bus: EventBus
) -> None:
    """Most passes move nothing, and a dashboard must not be woken for one."""
    assignment = await make_assignment(db)
    await make_visit(db, assignment)

    with bus.subscribe() as queue:
        await sweep_once(factory, bus, now=NOW)

    assert queue.empty()


async def test_a_failing_sweep_is_logged_and_the_pass_continues(
    db: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    bus: EventBus,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stated failure mode: one broken sweep must not silence the other two.

    A sweep that raises is the shape of a dropped connection or a lock timeout,
    and the wrong answer is a pass that aborts — the two working sweeps would
    then stop working for as long as the broken one stayed broken.
    """

    async def boom(session: AsyncSession, *, now: datetime) -> list[Event]:
        raise RuntimeError("the database went away")

    monkeypatch.setattr(sweeper, "SWEEPS", (boom, *sweeper.SWEEPS))

    assignment = await make_assignment(db, overdue_by_s=1.0)
    visit = await make_visit(db, assignment, silent_for_s=ABANDON_AFTER_S + 1)

    with caplog.at_level(logging.ERROR, logger="fieldproof.sweeper"):
        await sweep_once(factory, bus, now=NOW)

    assert "the database went away" in caplog.text
    assert (await reload_visit(db, visit)).state is VisitState.ABANDONED
    assert (await reload_assignment(db, assignment)).state is AssignmentState.EXPIRED


async def test_a_sweep_that_cannot_commit_publishes_nothing(
    db: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    bus: EventBus,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Events go out after the commit, so a rolled-back pass claims nothing happened.

    The failure this rules out is the expensive one: a dashboard told a visit was
    abandoned, a database in which it is still `ACTIVE`, and no correction coming
    until the client happens to reconnect and re-snapshot (ADR-0006).
    """

    async def moves_a_visit_then_writes_a_bad_row(
        session: AsyncSession, *, now: datetime
    ) -> list[Event]:
        visit = (await session.execute(select(Visit))).scalars().one()
        events = transition_visit(visit, VisitEvent.SILENCE_ELAPSED, at=now)
        assert events
        # Violates the foreign key, so the commit `sweep_once` owns is what fails
        # — after the events have been built and before they can be published.
        session.add(
            Visit(
                assignment_id=uuid4(),
                state=VisitState.ACTIVE,
                started_at=now,
                last_ping_at=now,
                created_at=now,
            )
        )
        return events

    monkeypatch.setattr(sweeper, "SWEEPS", (moves_a_visit_then_writes_a_bad_row,))

    assignment = await make_assignment(db)
    visit = await make_visit(db, assignment)

    with bus.subscribe() as queue, caplog.at_level(logging.ERROR, logger="fieldproof.sweeper"):
        await sweep_once(factory, bus, now=NOW)

    assert queue.empty()
    assert (await reload_visit(db, visit)).state is VisitState.ACTIVE
    assert "failed" in caplog.text


# ---------------------------------------------------------------- the row lock (issue 04)


async def test_a_visit_that_pinged_as_the_sweep_ran_is_not_abandoned(
    db: AsyncSession, factory: async_sessionmaker[AsyncSession], bus: EventBus
) -> None:
    """Issue 04's obligation, paid and proved.

    A visit is silent past the cutoff when the sweep's `SELECT` starts, and a
    ping lands before it commits. `with_for_update()` makes the sweep wait for
    that transaction, and Postgres then re-evaluates `last_ping_at < cutoff`
    against the row version it finds — which no longer matches — so the visit is
    spared rather than abandoned for silence it did not have.

    The blocked-while-uncommitted assertion is documentation, not the
    discriminator: without the lock the sweep's own `UPDATE` would still block
    here. What separates the two is the state at the end. Drop `with_for_update`
    and this fails on the last assertion, because an unlocked `SELECT` reads a
    snapshot in which the ping has not happened.
    """
    assignment = await make_assignment(db)
    visit = await make_visit(db, assignment, silent_for_s=ABANDON_AFTER_S + 1)

    # A ping's transaction, held open: it has the row lock and has moved
    # `last_ping_at` forward, but has not committed.
    locked = (
        await db.execute(select(Visit).where(Visit.id == visit.id).with_for_update())
    ).scalar_one()
    locked.last_ping_at = NOW

    pass_ = asyncio.create_task(sweep_once(factory, bus, now=NOW))
    done, _ = await asyncio.wait([pass_], timeout=0.2)
    assert not done, "the sweep should be waiting on the ping's row lock"

    await db.commit()
    await pass_

    assert (await reload_visit(db, visit)).state is VisitState.ACTIVE


async def test_a_report_filed_as_the_deadline_sweep_ran_is_not_overwritten(
    db: AsyncSession, factory: async_sessionmaker[AsyncSession], bus: EventBus
) -> None:
    """The same lock, on the sweep issue 08 will race hardest.

    A participant files their write-up in the last second of the window while
    the sweep is already selecting overdue visits. Without the lock the sweep
    reads a snapshot in which the visit is still `PENDING_REPORT` and then
    stamps `UNREPORTED` over a `COMPLETED` visit — losing a report that was
    filed on time, and leaving a `verdict` row attached to a visit the dashboard
    says was never written up.
    """
    assignment = await make_assignment(db)
    visit = await make_visit(
        db, assignment, state=VisitState.PENDING_REPORT, report_overdue_by_s=1.0
    )

    filed = (
        await db.execute(select(Visit).where(Visit.id == visit.id).with_for_update())
    ).scalar_one()
    filed.state = VisitState.COMPLETED

    pass_ = asyncio.create_task(sweep_once(factory, bus, now=NOW))
    await asyncio.wait([pass_], timeout=0.2)
    await db.commit()
    await pass_

    assert (await reload_visit(db, visit)).state is VisitState.COMPLETED


async def test_an_assignment_fulfilled_as_the_expiry_sweep_ran_stays_fulfilled(
    db: AsyncSession, factory: async_sessionmaker[AsyncSession], bus: EventBus
) -> None:
    """`ingest_ping` scopes its lock `of=Visit` to leave these rows to this sweep.

    The other writer is issue 08, moving an assignment to `FULFILLED` on the
    report that completes it. Losing this race would expire an assignment a
    business has already been delivered — and `EXPIRED` is terminal, so nothing
    puts it back.
    """
    assignment = await make_assignment(db, overdue_by_s=1.0)

    fulfilled = (
        await db.execute(select(Assignment).where(Assignment.id == assignment.id).with_for_update())
    ).scalar_one()
    fulfilled.state = AssignmentState.FULFILLED

    pass_ = asyncio.create_task(sweep_once(factory, bus, now=NOW))
    await asyncio.wait([pass_], timeout=0.2)
    await db.commit()
    await pass_

    assert (await reload_assignment(db, assignment)).state is AssignmentState.FULFILLED


# ---------------------------------------------------------------- one bus


def drain(queue: asyncio.Queue[Event]) -> list[Event]:
    """Everything published so far, in order."""
    return [queue.get_nowait() for _ in range(queue.qsize())]


async def test_the_dashboard_cannot_tell_a_sweep_from_a_request(
    db: AsyncSession,
    app: FastAPI,
    client: AsyncClient,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """spec.md §7's symmetry, from the consumer's side.

    One subscriber, one queue, two events: one produced by a participant's
    request and one by a sweep with no request behind it at all. They arrive as
    the same type carrying the same fields, and the only thing that separates
    them below is *which visit* they are about — there is no origin to sort on,
    which is exactly the property that makes issue 06 a renderer rather than two.
    """
    bus: EventBus = app.state.bus

    started = await make_assignment(db)
    silent = await make_assignment(db)
    silent_visit = await make_visit(db, silent, silent_for_s=ABANDON_AFTER_S + 1)

    with bus.subscribe() as queue:
        response = await client.post(f"/api/assignments/{started.id}/visits")
        assert response.status_code == 201
        await sweep_once(factory, bus, now=NOW)

    from_request, from_sweep = drain(queue)
    assert isinstance(from_request, VisitTransitioned)
    assert isinstance(from_sweep, VisitTransitioned)
    assert from_request.visit_id == UUID(response.json()["visit_id"])
    assert from_request.from_state is None
    assert from_request.to_state is VisitState.ACTIVE
    assert from_sweep.visit_id == silent_visit.id
    assert from_sweep.from_state is VisitState.ACTIVE
    assert from_sweep.to_state is VisitState.ABANDONED


async def test_a_ping_publishes_nothing(
    db: AsyncSession, app: FastAPI, client: AsyncClient
) -> None:
    """`ACTIVE --ping--> ACTIVE` is a move, not a change, and the bus carries changes.

    The dashboard's per-second liveness counter was cut (ADR-0006), so nothing
    renders a ping — and at `PING_INTERVAL_S` across a demo's worth of
    participants, publishing them would be the bus's entire traffic for no
    consumer.
    """
    bus: EventBus = app.state.bus

    assignment = await make_assignment(db)
    started = await client.post(f"/api/assignments/{assignment.id}/visits")
    visit_id = started.json()["visit_id"]

    with bus.subscribe() as queue:
        response = await client.post(
            f"/api/visits/{visit_id}/pings",
            json={
                "lat": 51.5080,
                "lng": -0.1281,
                "accuracy_m": 10.0,
                "reported_at": datetime.now(UTC).isoformat(),
            },
        )

    assert response.status_code == 202
    assert queue.empty()


A_VERIFICATION = Verification(
    verdict=Verdict.VERIFIED,
    inside_s=300.0,
    outside_s=60.0,
    unattributed_s=140.0,
    attributed_total_s=360.0,
    dwell_ratio=300 / 360,
    conclusive_pings=9,
    total_pings=10,
    visit_duration_s=500.0,
    radius_m=100.0,
    min_duration_s=300,
    scoring_config_version="v1",
)


def test_a_completed_delta_carries_its_verdict_and_no_other_delta_does() -> None:
    """The verdict is a fact about the transition, and the type says so (issue 08).

    `COMPLETED` without a breakdown is the state issue 08 calls one the
    dashboard cannot render; here it cannot be built. The converse holds too,
    so the verdict is not a field a sweep-produced event could ever carry —
    which is what keeps it from being an origin field in disguise (`events`).
    """
    visit_id, assignment_id = uuid4(), uuid4()

    completed = VisitTransitioned(
        visit_id=visit_id,
        assignment_id=assignment_id,
        from_state=VisitState.PENDING_REPORT,
        to_state=VisitState.COMPLETED,
        at=NOW,
        verdict=A_VERIFICATION,
    )
    assert completed.verdict is A_VERIFICATION

    with pytest.raises(ValueError, match="needs"):
        VisitTransitioned(
            visit_id=visit_id,
            assignment_id=assignment_id,
            from_state=VisitState.PENDING_REPORT,
            to_state=VisitState.COMPLETED,
            at=NOW,
        )
    with pytest.raises(ValueError, match="carries no"):
        VisitTransitioned(
            visit_id=visit_id,
            assignment_id=assignment_id,
            from_state=VisitState.ACTIVE,
            to_state=VisitState.ABANDONED,
            at=NOW,
            verdict=A_VERIFICATION,
        )


async def test_every_subscriber_gets_every_event(bus: EventBus) -> None:
    """Fan-out, and the removal that keeps it from leaking (issue 06's named risk)."""
    event = AssignmentTransitioned(
        assignment_id=uuid4(),
        from_state=AssignmentState.ASSIGNED,
        to_state=AssignmentState.EXPIRED,
        at=NOW,
    )
    with bus.subscribe() as first, bus.subscribe() as second:
        bus.publish([event])
        assert drain(first) == [event]
        assert drain(second) == [event]

        with bus.subscribe() as third:
            pass
        bus.publish([event])
        assert third.empty(), "a closed subscription must stop receiving"

    # Nothing is subscribed now, and publishing to nobody is not an error.
    bus.publish([event])


# ---------------------------------------------------------------- the loop


async def test_the_loop_keeps_sweeping(
    db: AsyncSession, factory: async_sessionmaker[AsyncSession], bus: EventBus
) -> None:
    """More than one pass, on its own timer, with nothing driving it.

    A single-pass test would pass against a `run_sweeper` that swept once and
    returned — which is the failure the whole module is arranged against, since
    a returned sweeper is indistinguishable from a quiet one.
    """
    assignment = await make_assignment(db)
    first = await make_visit(db, assignment, silent_for_s=ABANDON_AFTER_S + 1)

    async def abandoned(visit: Visit) -> bool:
        return (await reload_visit(db, visit)).state is VisitState.ABANDONED

    task = start_sweeper(factory, bus, tick_s=0.01)
    try:
        await eventually(lambda: abandoned(first))

        # A second visit, planted after the first pass has already been and gone.
        second_assignment = await make_assignment(db)
        second = await make_visit(db, second_assignment, silent_for_s=ABANDON_AFTER_S + 1)
        await eventually(lambda: abandoned(second))
    finally:
        await stop_sweeper(task)

    assert task.done()


async def test_the_loop_survives_a_pass_that_raises(
    factory: async_sessionmaker[AsyncSession],
    bus: EventBus,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`sweep_once` guards each sweep; this guards the scaffolding around them.

    Belt and braces on purpose. The loop ending is the one failure with no
    symptom, so the backstop is worth the line even though nothing currently
    reaches it.
    """
    passes = 0

    async def boom(*args: object, **kwargs: object) -> None:
        nonlocal passes
        passes += 1
        raise RuntimeError("the pass itself broke")

    async def swept_three_times() -> bool:
        return passes >= 3

    monkeypatch.setattr(sweeper, "sweep_once", boom)

    with caplog.at_level(logging.ERROR, logger="fieldproof.sweeper"):
        task = start_sweeper(factory, bus, tick_s=0.01)
        try:
            await eventually(swept_three_times)
        finally:
            await stop_sweeper(task)

    assert "the pass itself broke" in caplog.text


async def test_cancelling_the_sweeper_stops_it_cleanly(
    factory: async_sessionmaker[AsyncSession], bus: EventBus, caplog: pytest.LogCaptureFixture
) -> None:
    """Cancellation is the expected way out, so nothing is logged about it.

    Every logger, not just this module's: `_report_death` reads
    `task.exception()`, which *raises* on a cancelled task, and a done-callback
    that raises is reported by asyncio rather than by us. Scoping the assertion
    to `fieldproof.sweeper` would let a shutdown that throws on every restart go
    unnoticed, because the noise lands under `asyncio`.
    """
    with caplog.at_level(logging.DEBUG):
        task = start_sweeper(factory, bus, tick_s=0.01)
        await asyncio.sleep(0.05)
        await stop_sweeper(task)
        await asyncio.sleep(0)  # done-callbacks run on the next loop pass

    assert task.cancelled()
    assert [record for record in caplog.records if record.levelno >= logging.ERROR] == []


async def test_a_sweep_cancelled_mid_query_is_not_swallowed(
    factory: async_sessionmaker[AsyncSession], bus: EventBus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Shutdown lands *inside* a sweep, and the per-sweep handler must let it past.

    A slow query, a lock wait, a stalled connection: the realistic moment to be
    cancelled is mid-pass. `except Exception` does not catch `CancelledError`,
    which is a `BaseException` — widen it and the pass would log a shutdown as
    a failed sweep and carry on to the next one.

    Cancelling `sweep_once` rather than the whole loop keeps the failure
    bounded: either way the sweep's session is released on the way out, so a
    broken version fails the assertion instead of leaving a task holding a
    connection that the fixture teardown would then wait on.
    """
    entered = asyncio.Event()

    async def never_finishes(session: AsyncSession, *, now: datetime) -> list[Event]:
        entered.set()
        await asyncio.sleep(3600)
        return []

    monkeypatch.setattr(sweeper, "SWEEPS", (never_finishes,))

    task = asyncio.create_task(sweep_once(factory, bus, now=NOW))
    await asyncio.wait_for(entered.wait(), timeout=2.0)
    task.cancel()
    await asyncio.wait([task], timeout=2.0)

    assert task.cancelled(), "a cancelled sweep must not be logged and stepped over"


async def test_a_loop_cancelled_mid_pass_stops(
    factory: async_sessionmaker[AsyncSession], bus: EventBus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same for the loop's own handler, which the cancellation travels through next.

    `stop_sweeper` waits for the task, so a `run_sweeper` that caught
    `CancelledError` and went round again would hang shutdown for good rather
    than fail — which is why this cancels the task directly and asserts on it,
    instead of waiting on a `stop_sweeper` that would never return.
    """
    entered = asyncio.Event()

    async def never_finishes(*args: object, **kwargs: object) -> None:
        entered.set()
        await asyncio.sleep(3600)

    monkeypatch.setattr(sweeper, "sweep_once", never_finishes)

    task = start_sweeper(factory, bus, tick_s=0.01)
    await asyncio.wait_for(entered.wait(), timeout=2.0)
    task.cancel()
    await asyncio.wait([task], timeout=2.0)

    assert task.cancelled(), "the sweeper must stop when cancelled during a pass"


async def test_a_sweeper_that_dies_says_so(
    factory: async_sessionmaker[AsyncSession],
    bus: EventBus,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The failure mode this issue names: a dead sweeper that looks alive.

    `run_sweeper` never returns, so a finished task is always bad news — and a
    bare `create_task` swallows it, leaving a process that serves every request
    correctly while three transitions silently stop happening. The done-callback
    is the only thing that makes it audible.
    """

    async def dies(*args: object, **kwargs: object) -> None:
        raise RuntimeError("the loop fell over")

    monkeypatch.setattr(sweeper, "run_sweeper", dies)

    with caplog.at_level(logging.CRITICAL, logger="fieldproof.sweeper"):
        task = start_sweeper(factory, bus)
        with pytest.raises(RuntimeError):
            await task
        await asyncio.sleep(0)  # let the done-callback run

    assert "the sweeper stopped" in caplog.text
    assert "the loop fell over" in caplog.text


async def test_a_served_app_sweeps_onto_its_own_bus(db: AsyncSession) -> None:
    """The whole wiring, end to end, with nothing stubbed out.

    Never starting the sweeper is the loudest version of this issue's failure
    mode, and no request would ever reveal it: the `app` fixture runs no
    lifespan — deliberately, so that three sweeps do not run against every other
    test's fixtures — which means nothing else in this suite would notice.

    It has to be the *app's* bus, too. A sweeper handed a bus of its own would
    work perfectly and publish into a void, and every sweep test above would
    still pass, because they pass their own bus in. This is the one place the
    two halves of spec.md §7's "the same in-process bus the API handlers use"
    are checked against each other.

    No fast tick is needed: `run_sweeper` sweeps before it sleeps, so entering
    the lifespan is itself the first pass.

    **Every fixture here is built on the real clock, not `NOW`.** This is the
    one test whose sweeper reads `datetime.now`, and `make_assignment` anchors
    its deadline to the fixed `NOW` — an hour after noon on the day that
    constant names. Run this suite after 13:00 on that date and the expiry sweep
    fires too, and the assertion below sees an extra event. Both clocks in one
    test is the bug; the fixtures follow the sweeper's.
    """
    engine = create_engine(TEST_DATABASE_URL)
    app = create_app(engine=engine)
    try:
        now = datetime.now(UTC)
        assignment = new_assignment(
            business_name="Northwind Coffee",
            participant_name="Sam Okonjo",
            target_lat=51.5080,
            target_lng=-0.1281,
            deadline_at=now + timedelta(days=7),
            created_at=now,
        )
        db.add(assignment)
        await db.commit()

        visit = Visit(
            assignment_id=assignment.id,
            state=VisitState.ACTIVE,
            started_at=now - timedelta(seconds=ABANDON_AFTER_S + 60),
            last_ping_at=now - timedelta(seconds=ABANDON_AFTER_S + 1),
            created_at=now - timedelta(seconds=ABANDON_AFTER_S + 60),
        )
        db.add(visit)
        await db.commit()

        bus: EventBus = app.state.bus
        with bus.subscribe() as queue:
            async with app.router.lifespan_context(app):
                task = app.state.sweeper
                assert not task.done()
                await eventually(lambda: not_empty(queue))
            assert task.done()

        (event,) = drain(queue)
        assert isinstance(event, VisitTransitioned)
        assert event.visit_id == visit.id
        assert event.to_state is VisitState.ABANDONED
        assert (await reload_visit(db, visit)).state is VisitState.ABANDONED
    finally:
        await engine.dispose()


async def not_empty(queue: asyncio.Queue[Event]) -> bool:
    return not queue.empty()
