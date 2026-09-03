# SSE: snapshot then stream

Status: ready-for-agent
Blocked by: 05

`GET /api/dashboard/stream`. First event is the complete dashboard snapshot —
byte-identical to `GET /api/dashboard` — then deltas (ADR-0006).

Reconnection needs no `Last-Event-ID`, no replay buffer, no gap detection: the
client re-snapshots. Keep it that way; the correctness argument is the whole
reason this transport was chosen over polling.

In-process fan-out: a registry of `asyncio.Queue`, one per connected client,
removed on disconnect. Watch for the leak — a client that vanishes without
closing cleanly must not hold a queue forever.

Not tested (issue 10 rationale): a broken stream is loud in the demo.

## Comments

**2026-09-03 — implemented.** `src/fieldproof/dashboard.py` holds the snapshot
query and its shape, the `stream` protocol, and the SSE encoder; `api.py` gains
`GET /api/dashboard` and `GET /api/dashboard/stream`. `tests/test_dashboard.py`,
11 tests (238 total); ruff, ruff format, mypy strict and pytest all green. No new
dependencies.

**`GET /api/dashboard` came with it, and had to.** "Byte-identical to
`GET /api/dashboard`" names a route that did not exist, and nothing else owns
it. Both routes serialise through the one `model_dump_json` call, so the
identity is by construction; the GET returns a prebuilt `Response` rather than
letting FastAPI re-serialise the model on its own path. Checked by eye against
a served instance: the GET body and the `snapshot` event's `data:` line compared
equal as bytes.

**Subscribe first, snapshot second.** Every publisher commits and then
publishes, so a transition that commits after the snapshot's `SELECT` publishes
after it too, and a queue registered *before* the `SELECT` holds it. The other
order has a gap nothing corrects. The cost is a transition that commits just
before the query and publishes just after the subscription, which then arrives
twice; a delta carries the absolute `to_state`, so re-applying it is a no-op.
At-least-once with idempotent deltas is the contract issue 09's client should
be written to. One mutation, as fenced: the order swapped, and
`test_a_delta_racing_the_snapshot_is_not_lost` failed on its own while the
other ten passed. Reverted.

**One `SELECT`, not three.** Under `READ COMMITTED` three queries are three
snapshots, and a report committing between the visits query and the verdicts
query attaches a verdict to a visit the same payload shows as
`PENDING_REPORT`. One outer-joined statement sees one instant, which is what
"snapshot" is supposed to name.

**The snapshot's shape.** Assignments in creation order, each with every visit
ever made against it in start order, each visit with its verdict breakdown or
`null`. Attempt count is `len(visits)`, not a second field. No coordinates, no
pings, no accuracy, no `last_ping_at`, and no target location: ADR-0005 is
pinned as a property of the payload (`test_the_snapshot_carries_no_location_
evidence` plants a trail and walks every key of the dump) rather than a promise
about the UI. A `COMPLETED` visit with no verdict row — issue 08's "state the
dashboard cannot render" — renders as a completed visit with no verdict rather
than failing the whole snapshot.

**The leak, on every way out.** `stream` yields from inside
`EventBus.subscribe()`'s context manager and has no `finally` of its own. Three
tests, one per exit: the consumer closes the generator; the consumer's task is
cancelled while parked on the queue (the route Starlette takes when the socket
closes); the snapshot raises. `EventBus` gained a read-only `subscribers` count
so those tests can see the registry; nothing in the serving path reads it.

**Added beyond the letter of the fence: a keepalive comment frame.** A client
that vanishes *without* closing its socket — a phone leaving coverage — is
invisible until the server writes to it, and a stream parked on a quiet bus
never writes, so the context manager's exit would never be reached. `stream`
yields a `: keepalive` comment after `SSE_KEEPALIVE_S` (15s) of silence, which
turns "held forever" into "held for at most 15s". Standard SSE hygiene; it is
the mechanism that makes the fence's own answer to the leak reachable. One test.

**Found by eye, recorded not fixed: an open stream holds up shutdown.** On
SIGTERM uvicorn waits for in-flight responses before running the lifespan's
shutdown, with no default bound, and a stream is in flight until the tab
closes. Measured: one dashboard open, server still up 10s after SIGTERM; with
`--timeout-graceful-shutdown 2`, down in 2.2s and the sweeper stopped cleanly
after. Nothing in the app can shorten it — the lifespan hook that could tell
streams to stop runs after the wait. **Issue 10's README run command must carry
`--timeout-graceful-shutdown 5`.** Documented on `api.app`.

**Handed to issues 08 and 09: a delta is the bus event, and carries no
verdict.** `visit` deltas are `VisitTransitioned` as-is: `visit_id`,
`assignment_id`, `from_state`, `to_state`, `at`. That is what the fence asked
for and it is what keeps the sweep/request symmetry a property of the types.
It means a `COMPLETED` delta arrives without its breakdown. Two honest options,
neither taken here: carry the verdict on the event when issue 08 publishes it
(a verdict is a fact about the transition, not about its origin, so the
symmetry survives), or have the client re-snapshot on `COMPLETED` (zero new
code, one extra GET per report). Noted in issue 08's file.

**2026-09-04 — the handoff is settled by issue 08: the verdict rides the
delta.** `VisitTransitioned` gained `verdict: Verification | None`, present
exactly when `to_state` is `COMPLETED` and refused otherwise by the record's
own `__post_init__`. Against ADR-0005: the breakdown is precisely the set of
facts the business may see — it is the same `Verification` the verdict row is
written from, with no coordinate, ping, accuracy or `last_ping_at` on it, and
`test_a_completed_delta_carries_the_breakdown_and_no_location_evidence` walks
the encoded delta's keys the way the snapshot test walks the snapshot's. Against
the one-renderer property: a verdict is a fact about the transition, not about
its origin, and `COMPLETED` is a state only a report can reach, so a consumer
that branches on the verdict's presence learns nothing `to_state` had not
already told it — the sweep/request symmetry survives as a property of the
type. The re-snapshot alternative was rejected because it makes the client
treat one `to_state` differently from the other five (a GET on `COMPLETED`,
apply-in-place otherwise), which is a second code path for the state the
dashboard most needs to get right, and because the extra round trip buys
nothing the delta cannot carry. The shape differs from the snapshot's
`DashboardVerdict` in one field: the delta has no `computed_at`, because the
event's `at` is that instant. Issue 09's client should render a verdict from
either with one function.

**Not tested, as fenced.** The SSE grammar is not parsed in any test; the wire
was proved by a served instance on the test database — correct headers, named
`snapshot` and `visit` events, a visit started over HTTP arriving as a delta
inside the same second. The phone smoke test is the user's. No `Last-Event-ID`,
no replay, no per-ping events, no coalescing, no liveness counters.
