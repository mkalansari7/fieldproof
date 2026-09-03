"""The seed (issue 01, spec.md §9).

Three assignments against one participant. Two of them exist so that `EXPIRED`
and `UNREPORTED` — the two states nothing a demoer does can produce on demand —
are reachable inside a debrief rather than in 24 hours.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fieldproof.config import DEFAULT_REPORT_DEADLINE_S, SCORING_CONFIG
from fieldproof.schema import Assignment, Visit
from fieldproof.seed import (
    DEMO_WINDOW_S,
    NORMAL_ASSIGNMENT_ID,
    SHORT_DEADLINE_ASSIGNMENT_ID,
    SHORT_REPORT_DEADLINE_ASSIGNMENT_ID,
    seed,
)
from fieldproof.transitions import AssignmentState, VisitState

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


async def test_the_seed_creates_the_three_assignments(db: AsyncSession) -> None:
    await seed(db, now=NOW)

    ids = set((await db.scalars(select(Assignment.id))).all())
    assert ids == {
        NORMAL_ASSIGNMENT_ID,
        SHORT_DEADLINE_ASSIGNMENT_ID,
        SHORT_REPORT_DEADLINE_ASSIGNMENT_ID,
    }


async def test_all_three_belong_to_one_participant(db: AsyncSession) -> None:
    """Pre-assigned, not a pool to claim from (spec.md §10). One participant, three tasks."""
    await seed(db, now=NOW)

    participants = set((await db.scalars(select(Assignment.participant_name))).all())
    assert len(participants) == 1


async def test_reseeding_resets_rather_than_accumulates(db: AsyncSession) -> None:
    """The demo is re-run, and its deadlines are relative to the run. A seed that
    appended would leave the previous run's already-expired assignments on the
    dashboard, and one whose ids moved would break the participant links.

    The visit is planted by hand because the seed plants none: the rows a reset has
    to clear are the ones the *last demo* left behind, and a reseed that wiped the
    assignments while leaving their visits would violate the foreign key rather
    than fail quietly."""
    await seed(db, now=NOW)
    db.add(
        Visit(
            assignment_id=NORMAL_ASSIGNMENT_ID,
            state=VisitState.COMPLETED,
            started_at=NOW,
            ended_at=NOW + timedelta(minutes=10),
            last_ping_at=NOW + timedelta(minutes=10),
            created_at=NOW,
        )
    )
    await db.commit()

    await seed(db, now=NOW + timedelta(hours=4))

    ids = (await db.scalars(select(Assignment.id))).all()
    assert len(ids) == 3
    assert set(ids) == {
        NORMAL_ASSIGNMENT_ID,
        SHORT_DEADLINE_ASSIGNMENT_ID,
        SHORT_REPORT_DEADLINE_ASSIGNMENT_ID,
    }

    visits = await db.scalar(select(func.count()).select_from(Visit))
    assert visits == 0


async def test_the_normal_assignment_has_a_deadline_the_demo_will_not_trip(
    db: AsyncSession,
) -> None:
    await seed(db, now=NOW)

    assignment = await db.get(Assignment, NORMAL_ASSIGNMENT_ID)
    assert assignment is not None
    assert assignment.state is AssignmentState.ASSIGNED
    assert assignment.deadline_at > NOW + timedelta(days=1)


async def test_the_short_deadline_assignment_expires_inside_the_demo(
    db: AsyncSession,
) -> None:
    """The sweeper flips this one to `EXPIRED` while someone is watching (spec.md §7)."""
    await seed(db, now=NOW)

    assignment = await db.get(Assignment, SHORT_DEADLINE_ASSIGNMENT_ID)
    assert assignment is not None
    assert assignment.state is AssignmentState.ASSIGNED
    assert NOW < assignment.deadline_at <= NOW + timedelta(seconds=DEMO_WINDOW_S)


async def test_the_short_report_deadline_assignment_lapses_inside_the_demo(
    db: AsyncSession,
) -> None:
    """A visit ended here goes `UNREPORTED` while someone is watching (spec.md §5, §7),
    because the window is a column on the assignment rather than the global default."""
    await seed(db, now=NOW)

    assignment = await db.get(Assignment, SHORT_REPORT_DEADLINE_ASSIGNMENT_ID)
    assert assignment is not None
    assert 0 < assignment.report_deadline_s <= DEMO_WINDOW_S


async def test_only_that_assignment_shortens_the_report_window(db: AsyncSession) -> None:
    """The other two keep the real 24h window. A seed that shortened it everywhere
    would demo a product whose report deadline is three minutes."""
    await seed(db, now=NOW)

    for assignment_id in (NORMAL_ASSIGNMENT_ID, SHORT_DEADLINE_ASSIGNMENT_ID):
        assignment = await db.get(Assignment, assignment_id)
        assert assignment is not None
        assert assignment.report_deadline_s == DEFAULT_REPORT_DEADLINE_S


async def test_the_seed_plants_no_visits(db: AsyncSession) -> None:
    """Every visit state is reached by doing the thing, in any order.

    A seeded `PENDING_REPORT` visit would reach `UNREPORTED` one run sooner and cost
    more than it gave: `PENDING_REPORT` is non-terminal, so that row would hold
    `ux_visit_one_non_terminal_per_assignment` and 409 anyone starting a visit on
    that assignment (spec.md §6) until the sweeper lapsed it."""
    await seed(db, now=NOW)

    visits = await db.scalar(select(func.count()).select_from(Visit))
    assert visits == 0


async def test_every_seeded_assignment_can_reach_verified(db: AsyncSession) -> None:
    """`new_assignment` raises on incoherent terms, so this passing at all is the check.
    Asserted anyway: a seed that could only ever produce `unverifiable` would demo the
    product backwards.

    Read against `SCORING_CONFIG`, not a literal 180. Raising `sufficiency_s` is the
    change this assertion exists to catch, and a hardcoded threshold would keep
    passing through exactly that change while its name stopped being true."""
    await seed(db, now=NOW)

    assignments = (await db.scalars(select(Assignment))).all()
    assert assignments
    assert all(
        assignment.min_duration_s >= SCORING_CONFIG.sufficiency_s for assignment in assignments
    )
