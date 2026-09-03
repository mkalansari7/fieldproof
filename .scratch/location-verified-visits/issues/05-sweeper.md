# Background sweeper

Status: ready-for-agent
Blocked by: 01, 03

One `asyncio` task started on app startup, ticking `SWEEP_TICK_S`. Per `spec.md`
§7: stale `ACTIVE` → `ABANDONED`, overdue `PENDING_REPORT` → `UNREPORTED`,
overdue `ASSIGNED` → `EXPIRED`.

Publishes to the **same** in-process bus the API handlers use, so a dashboard
consumer cannot tell whether an event came from a request or a sweep. That
symmetry is what makes issue 06 simple.

Single-process by construction. Do not add a leader lock or advisory lock — the
limitation is deliberate and recorded once (ADR-0006, `docs/design.md`). Note it
in the code where a reader would otherwise assume it was an oversight.

Highest-risk item on Wednesday alongside issue 06. Buffer sits here.

## Comments

**2026-09-03 — implemented.** `src/fieldproof/sweeper.py` and
`src/fieldproof/events.py`, with `api.create_app` starting the task in its
lifespan and both handlers publishing through the new shared path.
`tests/test_sweeper.py`, plus `factory`, `bus` and `app` fixtures in
`conftest.py`. 44 new tests (227 total); ruff, ruff format, mypy strict and
pytest all green. No new dependencies.

**The bus came with it, and it had to.** "Publishes to the **same** bus the API
handlers use" is not a property the sweeper can have on its own — a bus only the
sweeper writes to satisfies the letter of it and none of the point. So
`events.py` holds the event types, the fan-out, and the one path that applies a
transition and produces the event recording it; `open_visit` and `ingest_ping`
were moved onto it in the same change. Issue 06 finds one bus because there was
never a second one.

The symmetry is a property of the types rather than a convention. `VisitTransitioned`
carries `visit_id`, `assignment_id`, `from_state`, `to_state`, `at` — no origin,
no `source`, no `via` — so a consumer *cannot* branch on which path produced an
event, and `test_the_dashboard_cannot_tell_a_sweep_from_a_request` is a
subscriber receiving one of each and sorting them only by which visit they name.

**A ping publishes nothing, by rule and not by exception.** `ACTIVE --ping-->
ACTIVE` is a real move and a real write to `last_ping_at`, but not a change of
state, and `transition_visit` returns an empty list for any self-loop. The
dashboard's per-second liveness view was cut (ADR-0006), so nothing renders a
ping, and at `PING_INTERVAL_S` across a demo they would be the bus's entire
traffic. Written as "publish when the state changed", applied to every caller,
rather than "pings are special", asserted at the ping handler.

It returns a *list* rather than `Event | None`, and that is a fix rather than a
preference — see the mutation accounting below. An optional invites the one
caller who knows it is always `None` to discard it, and a discarded return value
takes the rule with it.

**Issue 04's lock obligation, paid on all three sweeps.** Each one is
`SELECT ... FOR UPDATE`, because each reads a row and writes it after an `await`
— the shape 04's comment warned gets no lock for free. The abandon sweep is the
one 04 named, and it also buys the second direction: Postgres re-evaluates
`last_ping_at < cutoff` against the row version it finds after the lock wait, so
a visit that pinged microseconds before the cutoff drops out of the result
instead of being abandoned for silence it did not have. Waiting rather than
`SKIP LOCKED` is the point — skipping defers the row to the next tick, which is
also correct, but gets no re-check.

The other two are locked against a writer that does not exist yet, and both
races are issue 08's: a report filed in the last second of the window, against
the deadline sweep; and an assignment fulfilled by that report, against the
expiry sweep. Losing either would overwrite a terminal state with a different
terminal state — a filed report marked `UNREPORTED`, a delivered assignment
marked `EXPIRED` — and nothing puts those back. All three have a test that stages
the interleaving and fails without the lock.

**Three transactions per pass, not one.** A single transaction would make a pass
atomic, which sounds like a virtue and is not: the sweeps are independent, so one
failing on a locked row or a dropped connection would roll back the other two and
every tick would have to succeed at everything to accomplish anything.

**Events are published after the commit, never before.** An event is a claim that
a transition happened. Publishing before a commit that then fails leaves the
dashboard showing a visit that is still `ACTIVE` in the database, with no
correction until the client happens to reconnect and re-snapshot.

**`ended_at` is stamped to `last_ping_at` on abandon.** The spec does not say.
The alternatives are leaving it null, which forces every reader of a terminal
visit to write `ended_at or last_ping_at`, or using the sweep's own clock, which
credits the visit with the fifteen minutes of silence that killed it — and that
number is `verify`'s denominator. Every terminal visit now has an `ended_at`, and
it means the same thing in each: the last moment the visit was alive.

**A null `report_deadline_at` is not swept, deliberately.** SQL drops nulls from
`< now` on its own, and that is the wanted behaviour rather than an accident to
guard against. Such a row is a bug in whoever sealed the visit, and the failure
mode of sweeping it is taking away a participant's chance to write up work they
already did. `PENDING_REPORT` is visible and recoverable; `UNREPORTED` is
terminal and is not.

**Found here, settled in issue 08: an assignment expired beneath a live visit.**
spec.md §5 and §7 as first written made the rule unconditional, so a
participant who started a visit at 16:59 against a 17:00 deadline had the
assignment expire under them; their visit ran to `PENDING_REPORT` normally, and
then `advance_assignment` had no move out of `EXPIRED`, so the report they
filed could not fulfil it. Carving out "unless a visit is in flight" changed
what `EXPIRED` means, which was a spec decision and not an implementation one,
so it was implemented as written, pinned by
`test_an_assignment_expires_beneath_a_live_visit`, and routed forward. The
decision (2026-09-03, issue 08): the expiry sweep skips assignments with a
non-terminal visit, and `deadline_at` means *start by*. Landed with issue 08 on
2026-09-04 — the guard is a `NOT EXISTS` in
`sweeper.expire_overdue_assignments`, the pinning test became
`test_an_assignment_stays_assigned_beneath_a_live_visit`, and spec.md §5 and §7
now say so.

**The loop is arranged around one failure mode: a dead sweeper looks exactly
like a quiet one.** Nothing observes these transitions failing to happen — no
client is waiting on a response, and a visit that is never abandoned just stays
`ACTIVE` on a dashboard where `ACTIVE` is normal. So: each sweep is caught and
logged and the pass continues; the pass is caught and logged and the loop
continues; `except Exception` does not catch `CancelledError`, so shutdown is
never mistaken for a failure; and `start_sweeper` attaches a done-callback,
because a bare `create_task` leaves the exception sitting on a task nobody awaits
while the process keeps serving requests perfectly. `run_sweeper` sweeps before
it sleeps, so a process that has been down catches up on startup.

`stop_sweeper` grew a bound during the mutation campaign. A bare `await task`
hangs forever on a sweeper that swallows its cancellation, and the symptom would
be a server that never exits rather than a test that fails — so it uses
`asyncio.wait`, which never cancels twice and never blocks past its timeout, and
logs the give-up. A shutdown path with no upper bound is its own bug.

**Green on arrival, so mutation-checked rather than banked.** Twenty-two
mutations. Sixteen were caught on the first pass: no lock on each of the three
sweeps; each of the three deadline comparisons made inclusive; `ended_at` taken
from the sweep clock, and not written at all; events published before the commit;
the per-sweep handler removed; the loop handler removed; the done-callback
dropped; the loop made to return after one pass; each of the three `WHERE`
state filters dropped; nulls swept as overdue; and `open_visit` publishing
nothing.

Six were missed, and all six are closed:

- **The assignment deadline made inclusive.** A real gap — the other two
  boundaries had an exactly-at-the-edge case and this one only had ±1. Added.
- **The self-loop guard deleted.** The interesting one. `ingest_ping` *discarded*
  `transition_visit`'s return value, so the guard was unobservable from the only
  caller that exercised it: the rule could be deleted with no behaviour change
  anywhere. The test was not weak — the design was. `transition_visit` now
  returns a list and `ingest_ping` publishes it unexamined, so the emptiness has
  to be produced in the shared path to be observed at the handler.
- **The sweeper handed a bus of its own.** Nothing ran the lifespan *and* watched
  for events; every sweep test passes its own bus in, so a sweeper publishing
  into a void would have passed all of them. Now
  `test_a_served_app_sweeps_onto_its_own_bus` runs the real lifespan, the real
  task and the real bus end to end.
- **The lifespan never starting the sweeper.** Same test closes it.
- **`_report_death` without its `cancelled()` check.** The mutant does not log —
  it *raises*, because `task.exception()` raises on a cancelled task, and asyncio
  reports that under its own logger. The assertion was scoped to
  `fieldproof.sweeper` and saw nothing. Now scoped to every logger at `ERROR` and
  above.
- **`except BaseException` in the loop, swallowing cancellation.** Nothing
  cancelled the sweeper mid-pass; cancellation only ever landed in the `sleep`,
  outside the `try`. Two tests added, one per handler, each cancelling while a
  sweep is in flight — and both are bounded, because the first version of this
  test hung the suite instead of failing it, which is what prompted the
  `stop_sweeper` change above.

**Left undone, deliberately.** No leader lock and no advisory lock: single
process by construction, recorded once in ADR-0006 and named in the module
docstring so a reader does not take its absence for an oversight. `EventBus`
queues are unbounded — a bounded one would put a slow dashboard's backlog in the
sweeper's path, which is the wrong party to punish; `subscribe()` is a context
manager, so issue 06's named leak risk is handled by construction rather than by
the SSE handler remembering a `finally`. Nothing consumes `AssignmentTransitioned`
yet; issue 06 is the first renderer, and `Event` is a union so a variant it fails
to handle is a type error rather than a silently unrendered event.
