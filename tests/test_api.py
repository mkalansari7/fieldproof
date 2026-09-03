"""The trust boundary (issue 04, spec.md §4, §6).

These tests go through the real request path on purpose. Every rule in §4 is a
rule about what happens to something a *client* said, so a test that called the
handler's internals directly would be asserting the boundary from inside it.

The clock is likewise real. `received_at` has no seam (`api.received_now`), so
the tests assert the relationship — the server's stamp lands between the instant
the request went out and the instant the answer came back — rather than an
instant they chose. An assertion that could only pass because the test moved the
server's clock would not be an assertion about a trust boundary.
"""

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from fieldproof.api import Reason
from fieldproof.config import BACKFILL_GRACE_S
from fieldproof.schema import Assignment, Ping, Visit, new_assignment
from fieldproof.transitions import NON_TERMINAL_VISIT_STATES, AssignmentState, VisitState
from fieldproof.verification import Classification, classify

TARGET_LAT, TARGET_LNG = 51.5080, -0.1281
"""Trafalgar Square, as the seed uses. The radius around it is the default 100m."""

NOT_ACTIVE = sorted(set(VisitState) - {VisitState.ACTIVE}, key=lambda state: state.value)
"""Every state in which a ping is a 409. Derived, not listed: a sixth visit state
added to the machine becomes a case here without anyone remembering to add it."""

NON_TERMINAL = sorted(NON_TERMINAL_VISIT_STATES, key=lambda state: state.value)
TERMINAL = sorted(set(VisitState) - NON_TERMINAL_VISIT_STATES, key=lambda state: state.value)


async def make_assignment(
    db: AsyncSession,
    *,
    state: AssignmentState = AssignmentState.ASSIGNED,
    radius_m: float = 100.0,
) -> Assignment:
    """A committed assignment. Committed because the handler is on another connection."""
    now = datetime.now(UTC)
    assignment = new_assignment(
        business_name="Northwind Coffee",
        participant_name="Sam Okonjo",
        target_lat=TARGET_LAT,
        target_lng=TARGET_LNG,
        radius_m=radius_m,
        deadline_at=now + timedelta(days=7),
        created_at=now,
    )
    assignment.state = state
    db.add(assignment)
    await db.commit()
    return assignment


async def make_visit(
    db: AsyncSession, assignment: Assignment, state: VisitState = VisitState.ACTIVE
) -> Visit:
    """A visit placed directly in `state`.

    Constructed rather than started through the endpoint, which `schema.Visit`
    licenses for fixtures: reaching `UNREPORTED` by walking the machine would be
    testing the machine, and these cases are about what ingest does when it
    finds a visit already there.
    """
    now = datetime.now(UTC)
    visit = Visit(
        assignment_id=assignment.id,
        state=state,
        started_at=now,
        last_ping_at=now,
        created_at=now,
    )
    db.add(visit)
    await db.commit()
    return visit


def ping_payload(
    *,
    lat: float = TARGET_LAT,
    lng: float = TARGET_LNG,
    accuracy_m: float = 12.0,
    age_s: float = 0.0,
) -> dict[str, Any]:
    """A ping whose `reported_at` is `age_s` behind the client's clock."""
    reported_at = datetime.now(UTC) - timedelta(seconds=age_s)
    return {
        "lat": lat,
        "lng": lng,
        "accuracy_m": accuracy_m,
        "reported_at": reported_at.isoformat(),
    }


async def stored_pings(db: AsyncSession, visit: Visit) -> Sequence[Ping]:
    result = await db.execute(
        select(Ping).where(Ping.visit_id == visit.id).order_by(Ping.received_at)
    )
    return result.scalars().all()


# ---------------------------------------------------------------- what ingest stores


async def test_a_ping_is_accepted_and_stored(client: AsyncClient, db: AsyncSession) -> None:
    assignment = await make_assignment(db)
    visit = await make_visit(db, assignment)

    response = await client.post(f"/api/visits/{visit.id}/pings", json=ping_payload())

    assert response.status_code == 202

    (ping,) = await stored_pings(db, visit)
    assert ping.lat == TARGET_LAT
    assert ping.lng == TARGET_LNG
    assert ping.accuracy_m == 12.0
    assert ping.distance_m == pytest.approx(0.0, abs=1.0)


async def test_received_at_is_the_servers_clock_and_reported_at_is_kept_beside_it(
    client: AsyncClient, db: AsyncSession
) -> None:
    """spec.md §4's whole first half, in one row.

    The client's clock here is 30 seconds behind — inside the grace, so the ping
    is accepted — and the two columns must disagree by that much afterwards. A
    handler that stamped `received_at` from the payload would pass every other
    test in this file and fail this one.
    """
    assignment = await make_assignment(db)
    visit = await make_visit(db, assignment)
    payload = ping_payload(age_s=30.0)

    before = datetime.now(UTC)
    response = await client.post(f"/api/visits/{visit.id}/pings", json=payload)
    after = datetime.now(UTC)

    assert response.status_code == 202
    (ping,) = await stored_pings(db, visit)
    assert ping.received_at.tzinfo is not None
    assert before <= ping.received_at <= after
    assert ping.reported_at == datetime.fromisoformat(payload["reported_at"])
    assert (ping.received_at - ping.reported_at).total_seconds() == pytest.approx(30.0, abs=2.0)


async def test_a_ping_advances_last_ping_at(client: AsyncClient, db: AsyncSession) -> None:
    """The column the sweeper reads (spec.md §7), and the reason ingest writes at all."""
    assignment = await make_assignment(db)
    visit = await make_visit(db, assignment)
    opened_at = visit.last_ping_at

    response = await client.post(f"/api/visits/{visit.id}/pings", json=ping_payload())
    assert response.status_code == 202

    await db.refresh(visit)
    assert visit.last_ping_at > opened_at
    (ping,) = await stored_pings(db, visit)
    assert visit.last_ping_at == ping.received_at
    assert visit.state is VisitState.ACTIVE


@pytest.mark.parametrize(
    ("accuracy_m", "offset_deg", "expected"),
    [
        (12.0, 0.0, Classification.INSIDE),
        (12.0, 0.01, Classification.OUTSIDE),
        (400.0, 0.0, Classification.INCONCLUSIVE),
    ],
)
async def test_classification_is_verifications_answer_not_a_second_one(
    client: AsyncClient,
    db: AsyncSession,
    accuracy_m: float,
    offset_deg: float,
    expected: Classification,
) -> None:
    """The stored `classification` agrees with `classify` on the stored `distance_m`.

    Asserted against the function rather than against a hand-computed enum, so
    ADR-0003's rule stays in one place: a reimplementation in the handler that
    happened to be right today would still fail the moment `classify` changed,
    which is the drift worth catching. The literal expectation is there too, so
    a `classify` that broke in both places would not agree with itself quietly.
    """
    assignment = await make_assignment(db)
    visit = await make_visit(db, assignment)

    response = await client.post(
        f"/api/visits/{visit.id}/pings",
        json=ping_payload(lat=TARGET_LAT + offset_deg, accuracy_m=accuracy_m),
    )
    assert response.status_code == 202

    (ping,) = await stored_pings(db, visit)
    assert ping.classification is expected
    assert ping.classification is classify(
        distance_m=ping.distance_m,
        accuracy_m=ping.accuracy_m,
        radius_m=assignment.radius_m,
    )


# ---------------------------------------------------------------- the backfill grace


@pytest.mark.parametrize("age_s", [0.0, 30.0, BACKFILL_GRACE_S - 1])
async def test_a_ping_inside_the_grace_is_accepted(
    client: AsyncClient, db: AsyncSession, age_s: float
) -> None:
    """59s is the near side of the boundary issue 04 names.

    Exactly 60 is deliberately not a case. The server stamps `received_at` when
    the request arrives, milliseconds after the payload was built, so a ping
    constructed at exactly the grace is over it by the time it is judged — and
    that is the boundary working, not a flaw to route around with a fake clock.
    """
    assignment = await make_assignment(db)
    visit = await make_visit(db, assignment)

    response = await client.post(f"/api/visits/{visit.id}/pings", json=ping_payload(age_s=age_s))

    assert response.status_code == 202
    assert len(await stored_pings(db, visit)) == 1


async def test_a_ping_past_the_grace_is_rejected_and_writes_nothing(
    client: AsyncClient, db: AsyncSession
) -> None:
    """61s is the far side. Rejection means no row *and* no `last_ping_at` bump.

    The second half matters more than the first: a rejected ping that still
    refreshed `last_ping_at` would keep a visit alive on evidence the server
    just refused, which is the sweeper being lied to through the front door.

    Honest note on what that assertion catches. Writing `last_ping_at` before
    the grace check and leaving it there does *not* fail this test, because the
    rejection rolls the transaction back and the write never reaches the table —
    the transaction boundary, not the assertion, is what holds. It does fail
    against the implementation that would actually ship the bug: bumping the
    column and committing before validating.
    """
    assignment = await make_assignment(db)
    visit = await make_visit(db, assignment)
    opened_at = visit.last_ping_at

    response = await client.post(
        f"/api/visits/{visit.id}/pings", json=ping_payload(age_s=BACKFILL_GRACE_S + 1)
    )

    assert response.status_code == 422
    assert response.json()["detail"]["reason"] == Reason.PING_TOO_OLD
    assert await stored_pings(db, visit) == []
    await db.refresh(visit)
    assert visit.last_ping_at == opened_at
    assert visit.state is VisitState.ACTIVE


async def test_a_stale_ping_is_not_the_terminal_409(client: AsyncClient, db: AsyncSession) -> None:
    """The distinction the client's behaviour hangs on (spec.md §8).

    409 stops the interval for good. A stale reading must not, or a single
    cached fix served by `getCurrentPosition` on resume would end a visit the
    participant is still standing in — so the codes are different, and the next
    ping 15 seconds later is accepted normally.
    """
    assignment = await make_assignment(db)
    visit = await make_visit(db, assignment)

    stale = await client.post(
        f"/api/visits/{visit.id}/pings", json=ping_payload(age_s=BACKFILL_GRACE_S + 1)
    )
    fresh = await client.post(f"/api/visits/{visit.id}/pings", json=ping_payload())

    assert stale.status_code == 422
    assert fresh.status_code == 202
    assert len(await stored_pings(db, visit)) == 1


async def test_a_reported_at_in_the_future_is_stored_not_refused(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The grace is one-sided on purpose (spec.md §4).

    Only staleness is rejected, because only staleness is backfill. A clock
    running fast is skew like any other and nothing is scored on it, so keeping
    the row is strictly more useful than refusing it: the stored disagreement is
    the tamper signal the column exists for.
    """
    assignment = await make_assignment(db)
    visit = await make_visit(db, assignment)

    response = await client.post(f"/api/visits/{visit.id}/pings", json=ping_payload(age_s=-3600.0))

    assert response.status_code == 202
    (ping,) = await stored_pings(db, visit)
    assert ping.reported_at > ping.received_at


# ---------------------------------------------------------------- what the payload may say


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("accuracy_m", -50.0),
        ("lat", 91.0),
        ("lng", 181.0),
    ],
)
async def test_an_impossible_field_is_refused(
    client: AsyncClient, db: AsyncSession, field: str, value: float
) -> None:
    """A negative accuracy is the sharp one: it shrinks the uncertainty interval.

    `classify` reads `accuracy_m` as a half-width, so `accuracy_m: -1000` makes
    a ping 900m outside the radius conclusively *inside* it. Accuracy describes
    uncertainty and never trustworthiness (CONTEXT.md), which holds only while
    it is an accuracy at all.
    """
    assignment = await make_assignment(db)
    visit = await make_visit(db, assignment)

    response = await client.post(
        f"/api/visits/{visit.id}/pings", json=ping_payload() | {field: value}
    )

    assert response.status_code == 422
    assert await stored_pings(db, visit) == []


async def test_a_naive_reported_at_is_refused(client: AsyncClient, db: AsyncSession) -> None:
    """No zone, no guess. CLAUDE.md's timezone rule, held at the wire."""
    assignment = await make_assignment(db)
    visit = await make_visit(db, assignment)
    naive = datetime.now(UTC).replace(tzinfo=None).isoformat()

    response = await client.post(
        f"/api/visits/{visit.id}/pings", json=ping_payload() | {"reported_at": naive}
    )

    assert response.status_code == 422
    assert await stored_pings(db, visit) == []


async def test_a_ping_for_an_unknown_visit_is_404(client: AsyncClient) -> None:
    response = await client.post(f"/api/visits/{uuid4()}/pings", json=ping_payload())

    assert response.status_code == 404
    assert response.json()["detail"]["reason"] == Reason.NOT_FOUND


# ---------------------------------------------------------------- the 409, which is not an error


@pytest.mark.parametrize("state", NOT_ACTIVE, ids=lambda state: state.value)
async def test_a_ping_at_a_visit_that_is_not_active_is_409(
    client: AsyncClient, db: AsyncSession, state: VisitState
) -> None:
    """ "Not ACTIVE → 409. Always" (issue 04), for every state that is not ACTIVE.

    The handler never names `ACTIVE`: it runs `PING` through the machine and
    answers whatever the machine refuses. That is why this is parametrized over
    a derived set rather than four hand-written cases — the endpoint and the
    table cannot disagree about which states are pingable, because there is only
    the table.
    """
    assignment = await make_assignment(db)
    visit = await make_visit(db, assignment, state=state)

    response = await client.post(f"/api/visits/{visit.id}/pings", json=ping_payload())

    assert response.status_code == 409
    assert response.json()["reason"] == Reason.ILLEGAL_TRANSITION
    assert state.value in response.json()["message"]
    assert await stored_pings(db, visit) == []


async def test_a_closed_visit_outranks_a_stale_ping(client: AsyncClient, db: AsyncSession) -> None:
    """Both rules fire; the 409 wins, and the order is a decision (see `ingest_ping`).

    This is the resumed-iOS-tab case exactly: the tab wakes after a screen lock,
    the sweeper abandoned the visit while it slept, and the fix it has in hand
    is older than the grace. Answering 422 would be true and useless — the
    client would drop that reading and keep pinging a dead visit for another
    cycle before being told. The closed visit is the fact worth sending back.
    """
    assignment = await make_assignment(db)
    visit = await make_visit(db, assignment, state=VisitState.ABANDONED)

    response = await client.post(
        f"/api/visits/{visit.id}/pings", json=ping_payload(age_s=BACKFILL_GRACE_S + 1)
    )

    assert response.status_code == 409
    assert response.json()["reason"] == Reason.ILLEGAL_TRANSITION


async def test_a_ping_racing_a_seal_loses_and_leaves_no_row(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The race the row lock exists for, staged rather than hoped for.

    Another transaction seals this visit and holds the row lock without
    committing — which is what the sweeper's `UPDATE ... SET state='ABANDONED'`
    and issue 08's `POST /end` both look like from here. The ping arrives inside
    that window, waits, and re-reads a visit that is no longer `ACTIVE`.

    Drop `with_for_update` from `ingest_ping` and this fails with a 202 and a
    stored row: the unlocked `SELECT` sees the pre-`UPDATE` snapshot, judges the
    visit `ACTIVE`, and writes a ping whose `received_at` is after the trail was
    sealed. That is the trail `verify` documents as impossible, and the visible
    damage is a negative `unattributed_s` on the dashboard.
    """
    assignment = await make_assignment(db)
    visit = await make_visit(db, assignment)

    await db.execute(update(Visit).where(Visit.id == visit.id).values(state=VisitState.ABANDONED))

    request = asyncio.create_task(client.post(f"/api/visits/{visit.id}/pings", json=ping_payload()))
    await asyncio.sleep(0.2)
    assert not request.done(), "the ping should be waiting on the visit's row lock"

    await db.commit()
    response = await request

    assert response.status_code == 409
    assert await stored_pings(db, visit) == []


# ---------------------------------------------------------------- starting a visit


async def test_starting_a_visit_opens_it_active(client: AsyncClient, db: AsyncSession) -> None:
    assignment = await make_assignment(db)

    response = await client.post(f"/api/assignments/{assignment.id}/visits")

    assert response.status_code == 201
    visit = await db.get(Visit, UUID(response.json()["visit_id"]))
    assert visit is not None
    assert visit.state is VisitState.ACTIVE
    # Silence starts now, not at the first ping: a visit that never pings is
    # abandoned 15 minutes after it opened (`Visit.last_ping_at`).
    assert visit.last_ping_at == visit.started_at


async def test_starting_a_visit_on_an_unknown_assignment_is_404(client: AsyncClient) -> None:
    response = await client.post(f"/api/assignments/{uuid4()}/visits")

    assert response.status_code == 404


@pytest.mark.parametrize("state", NON_TERMINAL, ids=lambda state: state.value)
async def test_a_second_visit_is_refused_while_one_is_live(
    client: AsyncClient, db: AsyncSession, state: VisitState
) -> None:
    """ADR-0001's one-live-visit rule, reached through the machine rather than the index."""
    assignment = await make_assignment(db)
    await make_visit(db, assignment, state=state)

    response = await client.post(f"/api/assignments/{assignment.id}/visits")

    assert response.status_code == 409
    assert response.json()["reason"] == Reason.ILLEGAL_TRANSITION


@pytest.mark.parametrize("state", TERMINAL, ids=lambda state: state.value)
async def test_a_visit_may_be_retried_after_a_terminal_one(
    client: AsyncClient, db: AsyncSession, state: VisitState
) -> None:
    """Retrying is how a participant recovers from a dead phone (ADR-0001).

    `COMPLETED` is here for completeness and is unreachable in practice: a
    completed visit fulfils its assignment in the same stroke (ADR-0004), so the
    assignment would not still be `ASSIGNED`. The machine permits it; the
    assignment's state is what forbids it, one test down.
    """
    assignment = await make_assignment(db)
    await make_visit(db, assignment, state=state)

    response = await client.post(f"/api/assignments/{assignment.id}/visits")

    assert response.status_code == 201


@pytest.mark.parametrize(
    "state",
    [AssignmentState.EXPIRED, AssignmentState.FULFILLED],
    ids=lambda state: state.value,
)
async def test_a_visit_cannot_be_started_on_a_closed_assignment(
    client: AsyncClient, db: AsyncSession, state: AssignmentState
) -> None:
    assignment = await make_assignment(db, state=state)

    response = await client.post(f"/api/assignments/{assignment.id}/visits")

    assert response.status_code == 409
    assert response.json()["reason"] == Reason.ILLEGAL_TRANSITION


async def test_concurrent_starts_resolve_into_one_visit_and_clean_409s(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Five simultaneous starts. One 201, four 409s, one row — and no 500.

    Every one of the five reads the same "no visit here" and passes
    `start_visit`, because that check is a read and reads do not exclude each
    other. The partial unique index is what actually decides, and the losers
    reach it as an `IntegrityError` from the driver: unhandled, that is a 500
    and a stack trace for a rule the product enforces on purpose. Recognising
    the index by name turns it back into the machine's own refusal, so the four
    losers get the body the pre-check would have given them.
    """
    assignment = await make_assignment(db)

    responses = await asyncio.gather(
        *(client.post(f"/api/assignments/{assignment.id}/visits") for _ in range(5))
    )
    codes = sorted(response.status_code for response in responses)

    assert codes == [201, 409, 409, 409, 409]
    assert all(
        response.json()["reason"] == Reason.ILLEGAL_TRANSITION
        for response in responses
        if response.status_code == 409
    )
    visits = (
        (await db.execute(select(Visit).where(Visit.assignment_id == assignment.id)))
        .scalars()
        .all()
    )
    assert len(visits) == 1
