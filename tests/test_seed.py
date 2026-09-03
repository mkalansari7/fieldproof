"""The seed (issue 01, spec.md §9).

Three assignments against one participant. Two of them exist so that `EXPIRED`
and `UNREPORTED` — the two states nothing a demoer does can produce on demand —
are reachable inside a debrief rather than in 24 hours.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fieldproof.config import SCORING_CONFIG
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
    dashboard, and one whose ids moved would break the participant links."""
    await seed(db, now=NOW)
    await seed(db, now=NOW + timedelta(hours=4))

    ids = (await db.scalars(select(Assignment.id))).all()
    assert len(ids) == 3
    assert set(ids) == {
        NORMAL_ASSIGNMENT_ID,
        SHORT_DEADLINE_ASSIGNMENT_ID,
        SHORT_REPORT_DEADLINE_ASSIGNMENT_ID,
    }

    visits = await db.scalar(select(func.count()).select_from(Visit))
    assert visits == 1


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


async def test_the_short_report_deadline_visit_is_awaiting_a_report_it_will_not_get(
    db: AsyncSession,
) -> None:
    """`UNREPORTED` needs a visit already in `PENDING_REPORT` — a demoer cannot wait out
    the real 24h window, and `report_deadline_at` lives on the visit, not the assignment."""
    await seed(db, now=NOW)

    visit = (await db.scalars(select(Visit))).one()
    assert visit.assignment_id == SHORT_REPORT_DEADLINE_ASSIGNMENT_ID
    assert visit.state is VisitState.PENDING_REPORT
    assert visit.report_deadline_at is not None
    assert NOW < visit.report_deadline_at <= NOW + timedelta(seconds=DEMO_WINDOW_S)


async def test_the_seeded_visit_is_sealed(db: AsyncSession) -> None:
    """It left `ACTIVE`, so it has an `ended_at` and its trail is closed (spec.md §5)."""
    await seed(db, now=NOW)

    visit = (await db.scalars(select(Visit))).one()
    assert visit.ended_at is not None
    assert visit.started_at < visit.ended_at <= NOW


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
