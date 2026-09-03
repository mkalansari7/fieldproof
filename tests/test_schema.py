"""The schema's load-bearing constraints (issue 01, spec.md §2).

Not CRUD tests. Every case here covers something that is invisible when it is
wrong: a uniqueness rule the application would otherwise have to remember, a
timezone that silently goes missing on the way through the driver, or an enum
whose stored spelling drifts from the one the spec names.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from fieldproof.schema import (
    Assignment,
    Ping,
    Report,
    VerdictRecord,
    Visit,
    new_assignment,
)
from fieldproof.transitions import NON_TERMINAL_VISIT_STATES, VisitState
from fieldproof.verification import (
    Classification,
    IncoherentTermsError,
    ScoringConfig,
    Verdict,
)

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)

NON_TERMINAL = sorted(NON_TERMINAL_VISIT_STATES, key=lambda state: state.value)
"""Derived from `transitions`, not restated. A state added there extends these
cases automatically rather than leaving a hole nobody notices."""

TERMINAL = sorted(set(VisitState) - NON_TERMINAL_VISIT_STATES, key=lambda state: state.value)


async def make_assignment(db: AsyncSession, *, min_duration_s: int = 300) -> Assignment:
    assignment = new_assignment(
        business_name="Northwind Coffee",
        participant_name="Sam Participant",
        target_lat=51.5080,
        target_lng=-0.1281,
        deadline_at=NOW + timedelta(days=7),
        min_duration_s=min_duration_s,
        created_at=NOW,
    )
    db.add(assignment)
    await db.flush()
    return assignment


def make_visit(assignment: Assignment, state: VisitState, *, started_at: datetime = NOW) -> Visit:
    return Visit(
        assignment_id=assignment.id,
        state=state,
        started_at=started_at,
        last_ping_at=started_at,
        created_at=started_at,
    )


def make_ping(
    visit_id: UUID,
    *,
    reported_at: datetime = NOW,
    received_at: datetime = NOW,
) -> Ping:
    """A conclusively-inside ping. The two clocks are the only thing worth varying here."""
    return Ping(
        visit_id=visit_id,
        lat=51.5080,
        lng=-0.1281,
        accuracy_m=12.0,
        reported_at=reported_at,
        received_at=received_at,
        distance_m=4.2,
        classification=Classification.INSIDE,
    )


# ------------------------------------------------------------ ADR-0001 in the database


@pytest.mark.parametrize("second", NON_TERMINAL, ids=lambda state: state.value)
@pytest.mark.parametrize("first", NON_TERMINAL, ids=lambda state: state.value)
async def test_an_assignment_cannot_hold_two_non_terminal_visits(
    db: AsyncSession, first: VisitState, second: VisitState
) -> None:
    """At most one non-terminal visit (ADR-0001) is the database's rule, not the app's."""
    assignment = await make_assignment(db)
    db.add(make_visit(assignment, first))
    await db.flush()

    db.add(make_visit(assignment, second))
    with pytest.raises(IntegrityError):
        await db.flush()


@pytest.mark.parametrize("retry", NON_TERMINAL, ids=lambda state: state.value)
@pytest.mark.parametrize("finished", TERMINAL, ids=lambda state: state.value)
async def test_a_participant_may_start_again_once_the_previous_visit_is_terminal(
    db: AsyncSession, finished: VisitState, retry: VisitState
) -> None:
    """Retrying is how a participant recovers from a dead phone (ADR-0001)."""
    assignment = await make_assignment(db)
    db.add(make_visit(assignment, finished))
    await db.flush()

    db.add(make_visit(assignment, retry, started_at=NOW + timedelta(hours=1)))
    await db.flush()

    visits = (await db.scalars(select(Visit))).all()
    assert len(visits) == 2


async def test_terminal_visits_may_pile_up_against_one_assignment(db: AsyncSession) -> None:
    """The index constrains the non-terminal rows only. Attempt count is signal (ADR-0001)."""
    assignment = await make_assignment(db)
    for hour, state in enumerate(TERMINAL * 2):
        db.add(make_visit(assignment, state, started_at=NOW + timedelta(hours=hour)))
    await db.flush()

    visits = (await db.scalars(select(Visit))).all()
    assert len(visits) == len(TERMINAL) * 2


async def test_two_assignments_may_each_hold_an_active_visit(db: AsyncSession) -> None:
    """The uniqueness is per assignment. A partial index over the wrong column would
    still pass every test above and serialise the whole product."""
    first = await make_assignment(db)
    second = await make_assignment(db)
    db.add(make_visit(first, VisitState.ACTIVE))
    db.add(make_visit(second, VisitState.ACTIVE))
    await db.flush()

    visits = (await db.scalars(select(Visit))).all()
    assert len(visits) == 2


# ------------------------------------------------------------ one-to-one rows


async def test_a_visit_may_carry_only_one_report(db: AsyncSession) -> None:
    assignment = await make_assignment(db)
    visit = make_visit(assignment, VisitState.PENDING_REPORT)
    db.add(visit)
    await db.flush()

    db.add(Report(visit_id=visit.id, body="Counted the queue.", submitted_at=NOW))
    await db.flush()
    db.add(Report(visit_id=visit.id, body="Counted it again.", submitted_at=NOW))
    with pytest.raises(IntegrityError):
        await db.flush()


async def test_a_visit_may_carry_only_one_verdict(db: AsyncSession) -> None:
    """Re-scoring replaces a verdict, it does not accumulate them (ADR-0002)."""
    assignment = await make_assignment(db)
    visit = make_visit(assignment, VisitState.COMPLETED)
    db.add(visit)
    await db.flush()

    db.add(make_verdict(visit))
    await db.flush()
    db.add(make_verdict(visit))
    with pytest.raises(IntegrityError):
        await db.flush()


def make_verdict(visit: Visit, verdict: Verdict = Verdict.VERIFIED) -> VerdictRecord:
    return VerdictRecord(
        visit_id=visit.id,
        verdict=verdict,
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


# ------------------------------------------------------------ the driver boundary


async def test_timestamps_survive_the_round_trip_timezone_aware(db: AsyncSession) -> None:
    """`received_at` and `reported_at` mean different things (spec.md §4) and both are
    compared against a clock. A naive datetime coming back out is a correctness bug."""
    assignment = await make_assignment(db)
    visit = make_visit(assignment, VisitState.ACTIVE)
    db.add(visit)
    await db.flush()
    db.add(make_ping(visit.id, reported_at=NOW - timedelta(seconds=2), received_at=NOW))
    await db.flush()
    db.expire_all()

    stored = (await db.scalars(select(Ping))).one()
    assert stored.received_at.tzinfo is not None
    assert stored.reported_at.tzinfo is not None
    assert stored.received_at == NOW
    assert stored.reported_at == NOW - timedelta(seconds=2)

    stored_visit = (await db.scalars(select(Visit))).one()
    assert stored_visit.started_at.tzinfo is not None
    assert stored_visit.created_at.tzinfo is not None


async def test_a_ping_keeps_client_time_and_server_time_in_separate_columns(
    db: AsyncSession,
) -> None:
    """The two must not be merged. Skew is a tamper signal, and `received_at` is the
    sole basis for ordering and scoring (spec.md §4)."""
    assignment = await make_assignment(db)
    visit = make_visit(assignment, VisitState.ACTIVE)
    db.add(visit)
    await db.flush()
    skewed = NOW - timedelta(hours=3)
    db.add(make_ping(visit.id, reported_at=skewed, received_at=NOW))
    await db.flush()
    db.expire_all()

    stored = (await db.scalars(select(Ping))).one()
    assert stored.reported_at == skewed
    assert stored.received_at == NOW


async def test_the_verdict_column_stores_the_spec_s_own_spelling(db: AsyncSession) -> None:
    """`verified` / `suspicious` / `unverifiable`, lowercase, as spec.md §2 writes them.
    Storing the Python member names instead would put `VERIFIED` in the column and
    every hand-written query in the writeup would be wrong."""
    assignment = await make_assignment(db)
    visit = make_visit(assignment, VisitState.COMPLETED)
    db.add(visit)
    await db.flush()
    db.add(make_verdict(visit, Verdict.SUSPICIOUS))
    await db.flush()

    raw = await db.scalar(text("SELECT verdict::text FROM verdict"))
    assert raw == "suspicious"


# ------------------------------------------------------------ creation-time guards


def test_terms_that_could_never_verify_are_rejected_at_assignment_creation() -> None:
    """`min_duration_s >= SUFFICIENCY_S` (spec.md §1). The guard belongs here and
    deliberately not inside `verify`, where raising would break replay."""
    with pytest.raises(IncoherentTermsError):
        new_assignment(
            business_name="Northwind Coffee",
            participant_name="Sam Participant",
            target_lat=51.5080,
            target_lng=-0.1281,
            deadline_at=NOW + timedelta(days=7),
            min_duration_s=120,
            created_at=NOW,
        )


def test_the_terms_guard_reads_the_config_it_is_given() -> None:
    """`new_assignment` takes a `config` rather than reaching for the live thresholds.

    The same terms are coherent under one config and not under another, which is
    what makes the parameter load-bearing rather than decoration: the guard has to
    be testable against a threshold nobody has shipped yet, since raising
    `sufficiency_s` is exactly the change that would invalidate existing terms.
    """
    terms_at_240 = ScoringConfig(sufficiency_s=240)
    assignment = new_assignment(
        business_name="Northwind Coffee",
        participant_name="Sam Participant",
        target_lat=51.5080,
        target_lng=-0.1281,
        deadline_at=NOW + timedelta(days=7),
        min_duration_s=300,
        created_at=NOW,
        config=terms_at_240,
    )
    assert assignment.min_duration_s == 300

    with pytest.raises(IncoherentTermsError):
        new_assignment(
            business_name="Northwind Coffee",
            participant_name="Sam Participant",
            target_lat=51.5080,
            target_lng=-0.1281,
            deadline_at=NOW + timedelta(days=7),
            min_duration_s=300,
            created_at=NOW,
            config=ScoringConfig(sufficiency_s=600),
        )


def test_the_default_terms_are_coherent() -> None:
    assignment = new_assignment(
        business_name="Northwind Coffee",
        participant_name="Sam Participant",
        target_lat=51.5080,
        target_lng=-0.1281,
        deadline_at=NOW + timedelta(days=7),
        created_at=NOW,
    )
    assert assignment.radius_m == 100
    assert assignment.min_duration_s == 300


# ------------------------------------------------------------ the scan indexes


@pytest.mark.parametrize(
    ("table", "columns"),
    [
        ("assignment", "(state, deadline_at)"),
        ("visit", "(state, last_ping_at)"),
        ("ping", "(visit_id, received_at)"),
    ],
)
async def test_the_scan_indexes_exist(db: AsyncSession, table: str, columns: str) -> None:
    """The sweeper scans `assignment` and `visit` every `SWEEP_TICK_S` (spec.md §7) and
    verification reads a whole trail in `received_at` order. Missing indexes here are a
    sequential scan, which is invisible on seed data and not on anything else."""
    definitions = (
        await db.scalars(
            text(
                "SELECT indexdef FROM pg_indexes WHERE schemaname = current_schema() "
                "AND tablename = :table"
            ).bindparams(table=table)
        )
    ).all()
    assert any(columns in definition for definition in definitions), definitions


async def test_the_non_terminal_index_covers_exactly_the_non_terminal_states(
    db: AsyncSession,
) -> None:
    """The predicate has to agree with `transitions.NON_TERMINAL_VISIT_STATES`, which is
    the reason that frozenset is named rather than inlined."""
    predicate = await db.scalar(
        text(
            "SELECT indexdef FROM pg_indexes WHERE schemaname = current_schema() "
            "AND tablename = 'visit' AND indexdef LIKE '%WHERE%'"
        )
    )
    assert predicate is not None
    for state in VisitState:
        assert (f"'{state.value}'" in predicate) is (state in NON_TERMINAL_VISIT_STATES), state


async def test_a_ping_cannot_outlive_its_visit_id(db: AsyncSession) -> None:
    """Foreign keys are declared, not assumed. A ping with a dangling `visit_id` is a
    trail that verification will never find."""
    db.add(make_ping(uuid4()))
    with pytest.raises(IntegrityError):
        await db.flush()
