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

from fieldproof.config import DEFAULT_REPORT_DEADLINE_S
from fieldproof.database import clear_tables, create_engine, create_schema, session_factory
from fieldproof.schema import new_assignment

PARTICIPANT_NAME = "Sam Okonjo"
"""One pre-assigned participant, not a pool to claim from. The marketplace model is
named in the writeup's pushback section and deliberately not built (spec.md §10)."""

NORMAL_ASSIGNMENT_ID = UUID("00000000-0000-4000-8000-000000000001")
SHORT_DEADLINE_ASSIGNMENT_ID = UUID("00000000-0000-4000-8000-000000000002")
SHORT_REPORT_DEADLINE_ASSIGNMENT_ID = UUID("00000000-0000-4000-8000-000000000003")

DEMO_WINDOW_S = 180
"""How long a demoer waits for the sweeper to act. Three minutes: long enough to
finish a sentence about the sweeper before it proves the point, short enough that
nobody is watching a screen. It is the `deadline_at` offset on one assignment and
the `report_deadline_s` on another; no constant in `config` is changed to suit
the demo."""

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

    # UNREPORTED: a short report window, so a demoer starts a visit, ends it, and
    # watches the sweeper lapse it three minutes later (spec.md §5, §7) instead of
    # waiting out the 24h default.
    #
    # The window is a column on the assignment, which is what keeps this
    # order-independent: the seed plants no visit, so nothing holds the partial
    # unique index and this assignment can be demoed at any point. Seeding a visit
    # already in PENDING_REPORT would demo the same state one run earlier and 409
    # anyone starting a visit here until it lapsed.
    session.add(
        new_assignment(
            id=SHORT_REPORT_DEADLINE_ASSIGNMENT_ID,
            business_name="Pelago Pharmacy",
            participant_name=PARTICIPANT_NAME,
            target_lat=KINGS_CROSS_LAT,
            target_lng=KINGS_CROSS_LNG,
            deadline_at=now + timedelta(days=7),
            report_deadline_s=DEMO_WINDOW_S,
            created_at=now,
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

    print(f"Seeded 3 assignments for {PARTICIPANT_NAME}. No visits: every state is demoable.")
    print(f"  {'normal':<22} /a/{NORMAL_ASSIGNMENT_ID}")
    print(f"  {f'expires in {DEMO_WINDOW_S}s':<22} /a/{SHORT_DEADLINE_ASSIGNMENT_ID}")
    print(f"  {f'{DEMO_WINDOW_S}s report window':<22} /a/{SHORT_REPORT_DEADLINE_ASSIGNMENT_ID}")
    print(
        f"End a visit on the last one and leave it: UNREPORTED {DEMO_WINDOW_S}s later, "
        f"not {DEFAULT_REPORT_DEADLINE_S}s."
    )


if __name__ == "__main__":
    cli()
