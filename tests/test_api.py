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
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from fieldproof.api import Reason
from fieldproof.config import BACKFILL_GRACE_S, SCORING_CONFIG
from fieldproof.events import AssignmentTransitioned, EventBus, VisitTransitioned
from fieldproof.schema import Assignment, Ping, Report, VerdictRecord, Visit, new_assignment
from fieldproof.transitions import NON_TERMINAL_VISIT_STATES, AssignmentState, VisitState
from fieldproof.verification import (
    AssignmentTerms,
    Classification,
    Verdict,
    Verification,
    classify,
    verify,
)
from fieldproof.verification import Ping as TrailPing

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
    db: AsyncSession,
    assignment: Assignment,
    state: VisitState = VisitState.ACTIVE,
    *,
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
) -> Visit:
    """A visit placed directly in `state`.

    Constructed rather than started through the endpoint, which `schema.Visit`
    licenses for fixtures: reaching `UNREPORTED` by walking the machine would be
    testing the machine, and these cases are about what ingest does when it
    finds a visit already there.

    A sealed visit (`ended_at` given) gets the report deadline `end_visit` would
    have stamped, a day out, so the report tests are not racing the sweeper's
    definition of overdue.
    """
    started_at = datetime.now(UTC) if started_at is None else started_at
    visit = Visit(
        assignment_id=assignment.id,
        state=state,
        started_at=started_at,
        ended_at=ended_at,
        last_ping_at=started_at if ended_at is None else ended_at,
        report_deadline_at=None if ended_at is None else ended_at + timedelta(days=1),
        created_at=started_at,
    )
    db.add(visit)
    await db.commit()
    return visit


async def plant_ping(
    db: AsyncSession, visit: Visit, *, at: datetime, distance_m: float, accuracy_m: float
) -> Ping:
    """One stored ping at a chosen `received_at`, as ingest would have written it.

    `distance_m` is set directly rather than derived from coordinates: the
    report tests are about what `verify` is handed, and geodesy is `geo`'s
    (tested there). The coordinates are the target's and are not read by
    anything under test.
    """
    ping = Ping(
        visit_id=visit.id,
        lat=TARGET_LAT,
        lng=TARGET_LNG,
        accuracy_m=accuracy_m,
        reported_at=at,
        received_at=at,
        distance_m=distance_m,
        classification=classify(distance_m=distance_m, accuracy_m=accuracy_m, radius_m=100.0),
    )
    db.add(ping)
    await db.commit()
    return ping


async def stored_report(db: AsyncSession, visit: Visit) -> Report | None:
    return (
        await db.execute(select(Report).where(Report.visit_id == visit.id))
    ).scalar_one_or_none()


async def stored_verdict(db: AsyncSession, visit: Visit) -> VerdictRecord | None:
    return (
        await db.execute(select(VerdictRecord).where(VerdictRecord.visit_id == visit.id))
    ).scalar_one_or_none()


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


# ---------------------------------------------------------------- ending a visit (issue 08)

NOT_PENDING_REPORT = sorted(
    set(VisitState) - {VisitState.PENDING_REPORT}, key=lambda state: state.value
)


async def test_ending_a_visit_seals_it_and_stamps_the_report_deadline(
    client: AsyncClient, db: AsyncSession
) -> None:
    """`ACTIVE -> PENDING_REPORT`, `ended_at` from the server's clock, the deadline from the assignment's terms."""
    assignment = await make_assignment(db)
    visit = await make_visit(db, assignment)

    before = datetime.now(UTC)
    response = await client.post(f"/api/visits/{visit.id}/end")
    after = datetime.now(UTC)

    assert response.status_code == 200
    await db.refresh(visit)
    assert visit.state is VisitState.PENDING_REPORT
    assert visit.ended_at is not None
    assert before <= visit.ended_at <= after
    assert visit.report_deadline_at == visit.ended_at + timedelta(
        seconds=assignment.report_deadline_s
    )
    body = response.json()
    assert datetime.fromisoformat(body["ended_at"]) == visit.ended_at
    assert datetime.fromisoformat(body["report_deadline_at"]) == visit.report_deadline_at


@pytest.mark.parametrize("state", NOT_ACTIVE, ids=lambda state: state.value)
async def test_ending_a_visit_that_is_not_active_is_409(
    client: AsyncClient, db: AsyncSession, state: VisitState
) -> None:
    assignment = await make_assignment(db)
    visit = await make_visit(db, assignment, state=state)

    response = await client.post(f"/api/visits/{visit.id}/end")

    assert response.status_code == 409
    assert response.json()["reason"] == Reason.ILLEGAL_TRANSITION
    await db.refresh(visit)
    assert visit.state is state


async def test_ending_an_unknown_visit_is_404(client: AsyncClient) -> None:
    response = await client.post(f"/api/visits/{uuid4()}/end")

    assert response.status_code == 404
    assert response.json()["detail"]["reason"] == Reason.NOT_FOUND


async def test_a_ping_after_the_end_is_409_and_leaves_no_row(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The seal, from the trail's side: nothing received after `ended_at` is stored.

    This is the invariant `verify` documents and the whole reason `end_visit`
    takes the row lock. Here the two requests are sequential, which is the
    common case; the racing case is `test_a_ping_racing_a_seal_loses_and_
    leaves_no_row`.
    """
    assignment = await make_assignment(db)
    visit = await make_visit(db, assignment)
    assert (
        await client.post(f"/api/visits/{visit.id}/pings", json=ping_payload())
    ).status_code == 202

    assert (await client.post(f"/api/visits/{visit.id}/end")).status_code == 200
    late = await client.post(f"/api/visits/{visit.id}/pings", json=ping_payload())

    assert late.status_code == 409
    (only,) = await stored_pings(db, visit)
    await db.refresh(visit)
    assert visit.ended_at is not None
    assert only.received_at <= visit.ended_at


# ---------------------------------------------------------------- the report (issue 08)


async def test_end_then_report_completes_the_visit_and_fulfils_the_assignment(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The happy path, through every endpoint in order (spec.md §6).

    Start, ping, end, report. The visit lands `COMPLETED` with a report row and
    a verdict row stamped with the scoring config's version; the assignment
    lands `FULFILLED`. The verdict here is `suspicious` — a visit a few
    milliseconds long is shorter than `min_duration_s` — and the assignment is
    fulfilled anyway, which is ADR-0004 observed through the wire rather than
    asserted on the machine.
    """
    assignment = await make_assignment(db)
    started = await client.post(f"/api/assignments/{assignment.id}/visits")
    visit_id = UUID(started.json()["visit_id"])
    assert (
        await client.post(f"/api/visits/{visit_id}/pings", json=ping_payload())
    ).status_code == 202
    assert (await client.post(f"/api/visits/{visit_id}/end")).status_code == 200

    before = datetime.now(UTC)
    response = await client.post(f"/api/visits/{visit_id}/report", json={"body": "Shop was open."})
    after = datetime.now(UTC)

    assert response.status_code == 200
    visit = await db.get(Visit, visit_id)
    assert visit is not None
    assert visit.state is VisitState.COMPLETED
    await db.refresh(assignment)
    assert assignment.state is AssignmentState.FULFILLED

    report = await stored_report(db, visit)
    assert report is not None
    assert report.body == "Shop was open."
    assert before <= report.submitted_at <= after

    verdict = await stored_verdict(db, visit)
    assert verdict is not None
    assert verdict.verdict is Verdict.SUSPICIOUS
    assert verdict.scoring_config_version == SCORING_CONFIG.version
    assert verdict.radius_m == assignment.radius_m
    assert verdict.min_duration_s == assignment.min_duration_s
    assert verdict.total_pings == 1
    assert verdict.computed_at == report.submitted_at
    assert datetime.fromisoformat(response.json()["submitted_at"]) == report.submitted_at


async def test_a_report_before_the_end_is_409_and_scores_nothing(
    client: AsyncClient, db: AsyncSession
) -> None:
    """A trail that is not sealed is not judged: no report row, no verdict, assignment untouched."""
    assignment = await make_assignment(db)
    visit = await make_visit(db, assignment)

    response = await client.post(f"/api/visits/{visit.id}/report", json={"body": "Too early."})

    assert response.status_code == 409
    assert response.json()["reason"] == Reason.ILLEGAL_TRANSITION
    assert await stored_report(db, visit) is None
    assert await stored_verdict(db, visit) is None
    await db.refresh(visit)
    assert visit.state is VisitState.ACTIVE
    await db.refresh(assignment)
    assert assignment.state is AssignmentState.ASSIGNED


async def test_a_report_after_unreported_is_409(client: AsyncClient, db: AsyncSession) -> None:
    """`UNREPORTED` is terminal (spec.md §5): the window closed, and a late write-up does not reopen it."""
    assignment = await make_assignment(db)
    visit = await make_visit(
        db, assignment, state=VisitState.UNREPORTED, ended_at=datetime.now(UTC) - timedelta(days=2)
    )

    response = await client.post(f"/api/visits/{visit.id}/report", json={"body": "Too late."})

    assert response.status_code == 409
    assert response.json()["reason"] == Reason.ILLEGAL_TRANSITION
    assert await stored_report(db, visit) is None
    assert await stored_verdict(db, visit) is None
    await db.refresh(assignment)
    assert assignment.state is AssignmentState.ASSIGNED


@pytest.mark.parametrize("state", NOT_PENDING_REPORT, ids=lambda state: state.value)
async def test_a_report_at_a_visit_that_is_not_pending_report_is_409(
    client: AsyncClient, db: AsyncSession, state: VisitState
) -> None:
    """Every other state, derived: the handler runs the move and never names `PENDING_REPORT`."""
    assignment = await make_assignment(db)
    visit = await make_visit(db, assignment, state=state, ended_at=datetime.now(UTC))

    response = await client.post(f"/api/visits/{visit.id}/report", json={"body": "x"})

    assert response.status_code == 409
    assert state.value in response.json()["message"]


async def test_a_second_report_is_409_and_the_first_stands(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Double submit — a retried request, a double-tap — refused by the machine: the visit is `COMPLETED`."""
    assignment = await make_assignment(db)
    visit = await make_visit(
        db, assignment, state=VisitState.PENDING_REPORT, ended_at=datetime.now(UTC)
    )

    first = await client.post(f"/api/visits/{visit.id}/report", json={"body": "first"})
    second = await client.post(f"/api/visits/{visit.id}/report", json={"body": "second"})

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["reason"] == Reason.ILLEGAL_TRANSITION
    report = await stored_report(db, visit)
    assert report is not None
    assert report.body == "first"


async def test_a_report_row_without_its_transition_is_409_not_500(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The unique constraint, reached the only way it can be: a row that exists without its move.

    The machine cannot see this — the visit is still `PENDING_REPORT` — so the
    handler passes the check, scores the trail, and the INSERT is what refuses.
    Recognising `ONE_REPORT_PER_VISIT_INDEX` by name turns the driver's error
    into the machine's own 409 (`api._violated_index`); anything else would be
    a stack trace for a rule the schema enforces on purpose. Nothing else from
    the attempt survives the rollback: no verdict, no state change.
    """
    assignment = await make_assignment(db)
    visit = await make_visit(
        db, assignment, state=VisitState.PENDING_REPORT, ended_at=datetime.now(UTC)
    )
    db.add(Report(visit_id=visit.id, body="already here", submitted_at=datetime.now(UTC)))
    await db.commit()

    response = await client.post(f"/api/visits/{visit.id}/report", json={"body": "again"})

    assert response.status_code == 409
    assert response.json()["reason"] == Reason.ILLEGAL_TRANSITION
    assert await stored_verdict(db, visit) is None
    await db.refresh(visit)
    assert visit.state is VisitState.PENDING_REPORT
    await db.refresh(assignment)
    assert assignment.state is AssignmentState.ASSIGNED


async def test_a_report_racing_an_expiry_does_not_revive_the_assignment(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The handler's half of the fulfilment-vs-expiry lock, through the endpoint.

    `test_sweeper` proves the sweep waits on a handler that holds the
    assignment; this proves the handler holds it. Another transaction has the
    assignment row locked and is expiring it — the shape of the expiry sweep
    from here — and the report arrives inside that window. With `FOR UPDATE`
    on the assignment the handler waits, re-reads `EXPIRED`, and the machine
    refuses `VisitCompleted` with a 409; nothing is written.

    Scope the handler's lock to `of=Visit` and this fails with a 200: the
    unlocked read of the assignment sees `ASSIGNED`, the in-memory move to
    `FULFILLED` is legal, and the `UPDATE` then waits on the lock and writes
    `FULFILLED` over the `EXPIRED` that committed in the meantime — a lost
    update that revives a closed assignment. Found by mutation, and the reason
    the report handler's lock is not scoped the way `ingest_ping`'s is.
    """
    assignment = await make_assignment(db)
    visit = await make_visit(
        db, assignment, state=VisitState.PENDING_REPORT, ended_at=datetime.now(UTC)
    )

    await db.execute(
        update(Assignment)
        .where(Assignment.id == assignment.id)
        .values(state=AssignmentState.EXPIRED)
    )

    request = asyncio.create_task(
        client.post(f"/api/visits/{visit.id}/report", json={"body": "Racing."})
    )
    await asyncio.sleep(0.2)
    assert not request.done(), "the report should be waiting on the assignment's row lock"

    await db.commit()
    response = await request

    assert response.status_code == 409
    assert response.json()["reason"] == Reason.ILLEGAL_TRANSITION
    await db.refresh(assignment)
    assert assignment.state is AssignmentState.EXPIRED
    await db.refresh(visit)
    assert visit.state is VisitState.PENDING_REPORT
    assert await stored_report(db, visit) is None
    assert await stored_verdict(db, visit) is None


async def test_an_empty_report_is_422(client: AsyncClient, db: AsyncSession) -> None:
    assignment = await make_assignment(db)
    visit = await make_visit(
        db, assignment, state=VisitState.PENDING_REPORT, ended_at=datetime.now(UTC)
    )

    response = await client.post(f"/api/visits/{visit.id}/report", json={"body": ""})

    assert response.status_code == 422
    await db.refresh(visit)
    assert visit.state is VisitState.PENDING_REPORT


async def test_a_report_for_an_unknown_visit_is_404(client: AsyncClient) -> None:
    response = await client.post(f"/api/visits/{uuid4()}/report", json={"body": "x"})

    assert response.status_code == 404
    assert response.json()["detail"]["reason"] == Reason.NOT_FOUND


# ---------------------------------------------------------------- the verdict row


T0 = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
"""The hand-computed trail's start. Fixed, so every interval below is a subtraction."""

TRAIL: list[tuple[float, float, float]] = [
    # (seconds after T0, distance_m, accuracy_m)
    (0, 0.0, 10.0),  # inside
    (60, 0.0, 10.0),  # inside
    (90, 90.0, 50.0),  # inconclusive: 40..140 straddles the 100m radius; skipped, not a gap
    (120, 0.0, 10.0),  # inside
    (180, 0.0, 10.0),  # inside
    (240, 0.0, 10.0),  # inside
    (300, 500.0, 10.0),  # outside — the 240→300 pair disagrees and contributes nothing
    (360, 500.0, 10.0),  # outside
    (420, 0.0, 10.0),  # inside — the 360→420 pair disagrees too
    (480, 0.0, 10.0),  # inside
    (510, 0.0, 10.0),  # inside, but received after ended_at at +500: not in the trail
]
"""A 500-second visit with ten pings inside its window and one past it.

By hand: inside pairs 0→60, 60→120, 120→180, 180→240, 420→480 give 300s; the
one outside pair 300→360 gives 60s; attributed 360s of 500, unattributed 140s,
dwell 300/360. Nine conclusive of ten. If the stray at +510 were let in it
would add a 480→510 inside pair — inside 330s, unattributed 110s, eleven pings —
so every one of those numbers discriminates the bound `submit_report` applies.
"""


async def test_the_verdict_row_is_verify_over_the_sealed_trail(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The stored breakdown equals the hand-computed one, and equals `verify`'s.

    Two references on purpose, as the classification test does: the literals
    catch a `verify` that changed under us, and the call catches a handler that
    fed it something other than the sealed trail — the wrong duration, an
    unbounded query, a ping mapped wrongly. `visit_duration_s` is the server's
    two stamps and not the trail's span (480s), which is the other invariant.
    """
    assignment = await make_assignment(db)
    visit = await make_visit(
        db,
        assignment,
        state=VisitState.PENDING_REPORT,
        started_at=T0,
        ended_at=T0 + timedelta(seconds=500),
    )
    for offset_s, distance_m, accuracy_m in TRAIL:
        await plant_ping(
            db,
            visit,
            at=T0 + timedelta(seconds=offset_s),
            distance_m=distance_m,
            accuracy_m=accuracy_m,
        )

    response = await client.post(f"/api/visits/{visit.id}/report", json={"body": "Trail."})

    assert response.status_code == 200
    stored = await stored_verdict(db, visit)
    assert stored is not None
    assert stored.verdict is Verdict.VERIFIED
    assert stored.inside_s == 300.0
    assert stored.outside_s == 60.0
    assert stored.attributed_total_s == 360.0
    assert stored.unattributed_s == 140.0
    assert stored.dwell_ratio == pytest.approx(300 / 360)
    assert stored.conclusive_pings == 9
    assert stored.total_pings == 10
    assert stored.visit_duration_s == 500.0
    assert stored.radius_m == 100.0
    assert stored.min_duration_s == assignment.min_duration_s
    assert stored.scoring_config_version == SCORING_CONFIG.version

    expected = verify(
        ping_trail=[
            TrailPing(
                received_at=T0 + timedelta(seconds=offset_s),
                distance_m=distance_m,
                accuracy_m=accuracy_m,
            )
            for offset_s, distance_m, accuracy_m in TRAIL
            if offset_s <= 500
        ],
        terms=AssignmentTerms(
            radius_m=assignment.radius_m, min_duration_s=assignment.min_duration_s
        ),
        visit_duration_s=500.0,
        config=SCORING_CONFIG,
    )
    assert (
        Verification(
            **{field: getattr(stored, field) for field in Verification.__dataclass_fields__}
        )
        == expected
    )


async def test_a_report_publishes_the_completed_delta_with_its_verdict_then_the_fulfilment(
    app: FastAPI, client: AsyncClient, db: AsyncSession
) -> None:
    """What the dashboard gets, in order: the visit's move carrying its breakdown, then the assignment's.

    The verdict rides the delta (issue 06's handoff, decided in issue 08), so a
    connected dashboard renders the completed visit without a second query. It
    is the same `Verification` the row was written from — one computation, two
    destinations — and it carries no location evidence, which
    `test_dashboard` checks on the wire.
    """
    bus: EventBus = app.state.bus
    assignment = await make_assignment(db)
    visit = await make_visit(
        db,
        assignment,
        state=VisitState.PENDING_REPORT,
        started_at=T0,
        ended_at=T0 + timedelta(seconds=500),
    )

    with bus.subscribe() as queue:
        response = await client.post(f"/api/visits/{visit.id}/report", json={"body": "x"})

    assert response.status_code == 200
    stored = await stored_verdict(db, visit)
    assert stored is not None
    completed, fulfilled = [queue.get_nowait() for _ in range(queue.qsize())]
    assert completed == VisitTransitioned(
        visit_id=visit.id,
        assignment_id=assignment.id,
        from_state=VisitState.PENDING_REPORT,
        to_state=VisitState.COMPLETED,
        at=stored.computed_at,
        verdict=Verification(
            **{field: getattr(stored, field) for field in Verification.__dataclass_fields__}
        ),
    )
    assert fulfilled == AssignmentTransitioned(
        assignment_id=assignment.id,
        from_state=AssignmentState.ASSIGNED,
        to_state=AssignmentState.FULFILLED,
        at=stored.computed_at,
    )


async def test_ending_a_visit_publishes_the_seal(
    app: FastAPI, client: AsyncClient, db: AsyncSession
) -> None:
    bus: EventBus = app.state.bus
    assignment = await make_assignment(db)
    visit = await make_visit(db, assignment)

    with bus.subscribe() as queue:
        response = await client.post(f"/api/visits/{visit.id}/end")

    assert response.status_code == 200
    (sealed,) = [queue.get_nowait() for _ in range(queue.qsize())]
    assert isinstance(sealed, VisitTransitioned)
    assert sealed.from_state is VisitState.ACTIVE
    assert sealed.to_state is VisitState.PENDING_REPORT
    assert sealed.verdict is None
