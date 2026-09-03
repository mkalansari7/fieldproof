"""Demo data (spec.md §9). `python -m fieldproof.seed`.

Three assignments against one participant. Two of them exist only so that the
two server-originated states can be *seen*: `EXPIRED` and `UNREPORTED` are
produced by the sweeper with no request behind them (ADR-0006), and on real
timings they take a day to arrive.

Reseeding **resets**. The deadlines here are relative to the moment the seed
runs, so a re-run before a demo is what makes them short again; appending would
leave the previous run's already-expired rows on the dashboard.

The ids are fixed. A participant reaches an assignment by its id (spec.md §6),
and a reset that moved them would break every link and QR code pointing at the
demo.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from fieldproof.config import REPORT_DEADLINE_S
from fieldproof.database import clear_tables, create_engine, create_schema, session_factory
from fieldproof.schema import Visit, new_assignment
from fieldproof.transitions import VisitState

PARTICIPANT_NAME = "Sam Okonjo"
"""One pre-assigned participant, not a pool to claim from. The marketplace model is
named in the writeup's pushback section and deliberately not built (spec.md §10)."""

NORMAL_ASSIGNMENT_ID = UUID("00000000-0000-4000-8000-000000000001")
SHORT_DEADLINE_ASSIGNMENT_ID = UUID("00000000-0000-4000-8000-000000000002")
SHORT_REPORT_DEADLINE_ASSIGNMENT_ID = UUID("00000000-0000-4000-8000-000000000003")

SEEDED_VISIT_ID = UUID("00000000-0000-4000-8000-000000000010")

DEMO_WINDOW_S = 180
"""How long a demoer waits for the sweeper to act. Three minutes: long enough to
finish a sentence about the sweeper before it proves the point, short enough that
nobody is watching a screen. It stands in for `deadline_at` on one assignment and
for `REPORT_DEADLINE_S` on one visit; neither constant is changed to suit it."""

# Coordinates are real places so that the map in an internal audit view is not in
# the sea. Edit them before a phone smoke test — the point of that test is to
# stand inside the radius, and the radius is 100m around whatever is here.
TRAFALGAR_SQUARE_LAT, TRAFALGAR_SQUARE_LNG = 51.5080, -0.1281
BOROUGH_MARKET_LAT, BOROUGH_MARKET_LNG = 51.5055, -0.0910
KINGS_CROSS_LAT, KINGS_CROSS_LNG = 51.5308, -0.1238


async def seed(session: AsyncSession, *, now: datetime) -> None:
    """Reset the database to the three demo assignments (spec.md §9).

    `now` is passed in rather than read from a clock, matching the rest of the
    codebase: every timestamp below is relative to it, which is what makes the
    short deadlines assertable in a test rather than merely plausible.
    """
    await clear_tables(session)

    session.add(
        new_assignment(
            id=NORMAL_ASSIGNMENT_ID,
            business_name="Northwind Coffee",
            participant_name=PARTICIPANT_NAME,
            target_lat=TRAFALGAR_SQUARE_LAT,
            target_lng=TRAFALGAR_SQUARE_LNG,
            deadline_at=now + timedelta(days=7),
            created_at=now,
        )
    )

    # EXPIRED: the sweeper moves this one while someone is watching (spec.md §7).
    session.add(
        new_assignment(
            id=SHORT_DEADLINE_ASSIGNMENT_ID,
            business_name="Halcyon Books",
            participant_name=PARTICIPANT_NAME,
            target_lat=BOROUGH_MARKET_LAT,
            target_lng=BOROUGH_MARKET_LNG,
            deadline_at=now + timedelta(seconds=DEMO_WINDOW_S),
            created_at=now,
        )
    )

    # UNREPORTED: the state lives on a *visit*, not an assignment, and it is only
    # reachable from PENDING_REPORT. A demoer cannot produce one on demand — they
    # would have to end a visit and then wait out REPORT_DEADLINE_S — so the seed
    # supplies a visit already sealed and already late.
    #
    # The alternative was a per-assignment report window column, which would let a
    # demoer drive this end to end from the participant flow. Rejected: spec.md §1
    # lists exactly two columns that vary per assignment, REPORT_DEADLINE_S is a
    # global operational timing, and a third column added for the demo's benefit
    # would have to be read by the sweeper and the report handler forever after.
    #
    # The cost, which is real: PENDING_REPORT is non-terminal, so this row holds
    # the partial unique index against its assignment. Starting a visit on
    # Pelago Pharmacy is a 409 (spec.md §6) until the sweeper moves the row to
    # UNREPORTED. That resolves itself within DEMO_WINDOW_S and is the same 409
    # the participant flow already has to handle (issue 07), so it is left as is
    # — but it means this assignment is the one to demo *last*, and it is why the
    # other two carry no seeded visit.
    started_at = now - timedelta(minutes=20)
    ended_at = now - timedelta(minutes=5)
    session.add(
        new_assignment(
            id=SHORT_REPORT_DEADLINE_ASSIGNMENT_ID,
            business_name="Pelago Pharmacy",
            participant_name=PARTICIPANT_NAME,
            target_lat=KINGS_CROSS_LAT,
            target_lng=KINGS_CROSS_LNG,
            deadline_at=now + timedelta(days=7),
            created_at=now,
        )
    )
    session.add(
        Visit(
            id=SEEDED_VISIT_ID,
            assignment_id=SHORT_REPORT_DEADLINE_ASSIGNMENT_ID,
            state=VisitState.PENDING_REPORT,
            started_at=started_at,
            ended_at=ended_at,
            last_ping_at=ended_at,
            report_deadline_at=now + timedelta(seconds=DEMO_WINDOW_S),
            created_at=started_at,
        )
    )

    await session.commit()


async def main() -> None:
    """Build the schema if it is missing, then reset the demo data."""
    engine = create_engine()
    try:
        await create_schema(engine)
        async with session_factory(engine)() as session:
            await seed(session, now=datetime.now(UTC))
    finally:
        await engine.dispose()


def cli() -> None:
    """Entry point. Prints outside the coroutine, so the `ASYNC` rule stays absolute.

    `print` blocks on stdout. Nothing else shares this process's loop, so it would
    be harmless here — but "no blocking calls inside `async def`" (CLAUDE.md) is
    worth more as a rule with no benign exceptions than as one every reader has to
    re-adjudicate.
    """
    asyncio.run(main())

    print(f"Seeded 3 assignments for {PARTICIPANT_NAME}.")
    print(f"  normal                 /a/{NORMAL_ASSIGNMENT_ID}")
    print(f"  expires in {DEMO_WINDOW_S}s        /a/{SHORT_DEADLINE_ASSIGNMENT_ID}")
    print(f"  unreported in {DEMO_WINDOW_S}s     /a/{SHORT_REPORT_DEADLINE_ASSIGNMENT_ID}")
    print(f"The real report window is {REPORT_DEADLINE_S}s; the seeded visit is already late.")
    print("Start a visit on Pelago Pharmacy last: its seeded visit 409s until it lapses.")


if __name__ == "__main__":
    cli()
