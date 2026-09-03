# Ping ingest and the trust boundary

Status: ready-for-agent
Blocked by: 01, 02

`POST /api/visits/{id}/pings`. INSERT-only: compute `distance_m` and
`classification` once at write and store them, so verification stays an aggregate
(ADR-0002).

Rules from `spec.md` §4, each with a test:

- `received_at` server-stamped; `reported_at` stored but never scored on.
- Not `ACTIVE` → 409. Always.
- `reported_at` older than `BACKFILL_GRACE_S` → rejected. Test **both sides** of
  the boundary, 59s and 61s.
- Updates `visit.last_ping_at` — this is what the sweeper reads.

The 409 is not an error path, it is the expected outcome of a phone locked past
15 minutes. Return something the client can act on (issue 07).

## Comments

**2026-09-03 — implemented.** `src/fieldproof/api.py`, `TargetLocation` in
`geo.py`, `Assignment.target` and `ONE_NON_TERMINAL_VISIT_INDEX` in `schema.py`,
`tests/test_api.py` and a `client` fixture in `conftest.py`. 33 new tests (184
total); ruff, ruff format, mypy strict and pytest all green. `fastapi` added to
dependencies, `httpx` to dev, and the `pydantic.mypy` plugin switched on where
`pyproject.toml` said to switch it on when FastAPI landed.

**Scope: the start endpoint came with it.** `POST /api/assignments/{id}/visits`
is in this change, not only `POST /api/visits/{id}/pings`. It is the other half
of the same boundary — the ACTIVE visit ingest requires does not otherwise exist
outside a fixture, and the concurrent-start race is the only place the partial
unique index is load-bearing at request time. `POST /end` and `POST /report`
stay with issue 08.

**`TargetLocation`, introduced.** Issue 01 declined it for want of a second
caller; ingest is that caller, reading all three columns for every ping. It
lives in `geo.py`, not `verification.py`, and `AssignmentTerms` keeps its own
`radius_m` rather than nesting it: `verify` needs the radius and must never
acquire a coordinate, and composing the two would force every re-scoring caller
to supply two floats judgement does not read. It is a type with a method rather
than a record, because `haversine_m` takes four interchangeable floats and
transposing the target with the reported position is silent, symmetric and
wrong. `Assignment.target` exists now; `Assignment.terms` still does not, for
the same reason it did not before.

**Stale ping is 422, and it is not the same rejection as 409.** The argument for
calling it a conflict is that both are "the server will not take this" — but 409
is about the resource's state, and a stale ping conflicts with nothing about the
visit: the same visit accepts the next ping fifteen seconds later. The two
answers ask opposite things of the client. spec.md §8 makes 409 *terminal* —
stop the interval, release the Wake Lock, offer a new visit — and
`getCurrentPosition` can serve a cached fix on resume, so a stale reading
answered 409 would end a visit the participant is still standing in. 422 keeps
the client's rule total: 409 stops, any other 4xx drops one reading. FastAPI
already answers 422 for a malformed payload and staleness is the same family —
the payload is well-formed and not acceptable, and it is decided from the
payload and the server clock alone, without reading the visit at all. The bodies
carry a `reason` so the two 422s are still distinguishable; §4 and §6 updated.

**Both can hold, and 409 wins.** An iOS tab waking from a screen lock posts a
fix older than the grace at a visit the sweeper abandoned while it slept — the
measured case, not a hypothetical. State is checked first: no payload could have
succeeded, so "fix the payload and retry" is a lie, and the participant is told
a cycle earlier than 422-then-409 would tell them. Tested.

**Row lock: recommended, and here is the race.** Not `last_ping_at` — two pings
racing on that column are last-writer-wins over a value the sweeper compares
against a 900-second window, and the loser is out by the width of one request.
Not visit start either; the partial unique index referees that and this change
leans on it. The race is between reading the visit's state and writing the ping.
`advance_visit` reads `ACTIVE`, and the INSERT that follows crosses an `await`,
during which the sweeper's abandon (§7) or issue 08's `POST /end` (§5) can
commit. Under `READ COMMITTED` an unlocked `SELECT` is a snapshot, so that
commit is invisible and the ping lands on a visit that is already sealed: a
trail that was closed and then grew. `verify` names that invariant explicitly
and says what breaks — `attributed_total_s` exceeding the visit duration, so
`unattributed_s` renders negative on the dashboard. `SELECT ... FOR UPDATE OF
visit` makes the check and the write one decision; `of=Visit` keeps the lock off
the assignment row, which the sweeper's `EXPIRED` scan writes to for unrelated
reasons. Contention is one request per visit per fifteen seconds.

The second direction is a bonus worth naming: with the lock, the sealer waits
for the ping's transaction and Postgres re-checks its own
`WHERE last_ping_at < cutoff` against the row we just wrote, so a visit that
pinged microseconds before the cutoff is not abandoned for silence.

The obligation this creates on issues 05 and 08: the sweeper and `POST /end`
must take the visit's row lock too. A plain `UPDATE ... WHERE id = ... AND state
= 'ACTIVE'` gets it for free, since Postgres locks the rows it updates — but a
read-then-write across an `await`, the shape this handler had to avoid, does
not.

**Tests are trust-boundary tests, driven over ASGI with a real clock.**
`received_now()` has no seam on purpose: an injectable clock is the ordinary
testable choice and the wrong one for the single claim this endpoint makes, so
the tests assert that the stamp lands between the instant the request went out
and the instant the answer arrived. The grace boundary is tested at 59s and 61s.
Exactly 60 is deliberately not a case: the server stamps on arrival,
milliseconds after the payload was built, so a ping constructed at exactly the
grace is over it by the time it is judged — which is the boundary working, and
not a reason to introduce a fake clock. The 409 cases are parametrized over
`set(VisitState) - {ACTIVE}` rather than four hand-written states.

Two rejections the issue did not ask for and the boundary needs. `accuracy_m` is
`ge=0`: `classify` reads it as a half-width, so a negative accuracy *shrinks*
the uncertainty interval and turns a ping 900m outside the radius conclusively
inside it — accuracy describes uncertainty and never trustworthiness
(CONTEXT.md), but only while it is an accuracy. And `reported_at` is
`AwareDatetime`, so a naive client timestamp is refused rather than assigned a
zone by guess.

**Green on arrival again, so mutation-checked rather than banked.** Thirteen
mutations, twelve caught: no row lock; `received_at` taken from the payload; no
grace check; grace checked before state; no state check at all; the
`IntegrityError` left to escape as a 500; `last_ping_at` not advanced;
`accuracy_m` unconstrained; `reported_at` naive-tolerant; classification
computed with the accuracy discarded; `Assignment.target` transposing lat and
lng; and the started visit's `last_ping_at` not matching `started_at`.

The thirteenth was missed and is worth the honesty: writing `last_ping_at`
before the grace check and leaving it there passes every test, because the
rejection rolls the transaction back and the write never reaches the table. The
assertion is not what holds there — the transaction boundary is. It does catch
the version that would actually ship the bug, bumping the column and committing
before validating, which was run separately and failed as it should. Noted on
the test.

**Left undone, deliberately.** No `uvicorn` dependency: nothing calls it yet and
this repo's precedent is to move on a real caller. `fieldproof.api:app` is there
for it, and issue 07 is where a running server is first needed — `pip install
uvicorn` will be part of that change, not this one. The 409 body carries
`reason` and the machine's own message but not the visit's current state as a
field; if issue 07 wants to word the page differently for a visit the
participant ended in another tab versus one the sweeper abandoned, the single
exception handler is the one place to add it.
