"""The in-process event bus, and the one path that produces events (spec.md §7).

Three of this system's transitions — `ABANDONED`, `UNREPORTED`, `EXPIRED` — have
no HTTP request behind them (ADR-0006). That is the whole reason the dashboard is
pushed rather than polled, and it is why this module exists: the sweeper and the
API handlers must publish the *same* events through the *same* bus, so a
dashboard consumer cannot tell which path produced one.

That symmetry is a property of the event types, not a convention the two callers
agree to keep. `VisitTransitioned` carries no origin, no `source`, no `via`
field, and there is nowhere for one to be added without a consumer immediately
being able to branch on it. A test that subscribes, starts a visit over HTTP and
runs a sweep receives two values of one type and has no way to sort them.

Transitions are applied here rather than at the call sites, for the same reason
`api._illegal_transition` lives in one place: `advance_visit` and the event that
records it are one act, and a caller that could do the first without the second
is a caller that can silently stop feeding the dashboard.
"""

import asyncio
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from fieldproof.schema import Assignment, Visit
from fieldproof.transitions import (
    AssignmentEvent,
    AssignmentState,
    VisitEvent,
    VisitState,
    advance_assignment,
    advance_visit,
)
from fieldproof.verification import Verification


@dataclass(frozen=True)
class VisitTransitioned:
    """A visit changed state. The unit the dashboard renders.

    `from_state` is `None` for a visit that has just started, matching spec.md
    §5's transition table, whose start row has no from-state at all, and
    `transitions.start_visit`, which takes `latest_visit=None` for the same
    reason. A visit's first appearance is not a move out of anything.

    **`verdict` is present exactly when `to_state` is `COMPLETED`, and it is
    not an origin field.** A verdict is a fact about the transition — the move
    into `COMPLETED` *is* the trail being judged (spec.md §5) — so the delta
    carries it the way the snapshot does, and the dashboard renders one shape
    from either (issue 06's handoff, settled in issue 08). The symmetry this
    module exists for survives because `COMPLETED` is only ever reached by a
    report: a consumer that branches on the verdict's presence learns nothing
    it did not already have from `to_state`. What it carries is the breakdown
    and nothing else — no coordinates, no pings, no accuracy figures — which is
    the line ADR-0005 draws and the snapshot's own test walks. The check in
    `__post_init__` makes "a `COMPLETED` delta with no verdict" unconstructible
    rather than merely unrendered.
    """

    visit_id: UUID
    assignment_id: UUID
    from_state: VisitState | None
    to_state: VisitState
    at: datetime
    verdict: Verification | None = None

    def __post_init__(self) -> None:
        if (self.to_state is VisitState.COMPLETED) != (self.verdict is not None):
            raise ValueError(
                f"a transition to {self.to_state.value} "
                f"{'needs' if self.verdict is None else 'carries no'} verdict"
            )


@dataclass(frozen=True)
class AssignmentTransitioned:
    """An assignment changed state: `EXPIRED` by the sweep, `FULFILLED` by a report."""

    assignment_id: UUID
    from_state: AssignmentState
    to_state: AssignmentState
    at: datetime


type Event = VisitTransitioned | AssignmentTransitioned
"""Everything that goes on the bus.

A union of records, like `transitions.AssignmentEvent` and for the same reason: a
variant added here and not handled by the dashboard's renderer is a type error
rather than a silently unrendered event.
"""


def visit_started(visit: Visit) -> Event:
    """The event for a visit that has just been opened (`api.open_visit`).

    Not a transition — `start_visit` is not in the visit table and there is no
    START event to feed `advance_visit` — so this is a separate function rather
    than a `from_state=None` special case inside `transition_visit`.
    """
    return VisitTransitioned(
        visit_id=visit.id,
        assignment_id=visit.assignment_id,
        from_state=None,
        to_state=visit.state,
        at=visit.started_at,
    )


def transition_visit(
    visit: Visit, event: VisitEvent, *, at: datetime, verdict: Verification | None = None
) -> list[Event]:
    """Advance `visit` in place and return what to publish. Empty if nothing changed.

    `verdict` is the report path's alone: `REPORT_SUBMITTED` is the one event
    whose move lands in `COMPLETED`, and `VisitTransitioned` refuses that move
    without a verdict and any other move with one. The caller has therefore
    scored the trail *before* calling this, which is the order `api.submit_report`
    documents.

    Empty is what a ping gets. `ACTIVE --ping--> ACTIVE` is a real move in the
    machine and a real write to `last_ping_at`, but it is not a change of state,
    and the dashboard does not render one — the live "last seen Ns ago" counter
    that would have wanted per-ping events was scoped out (ADR-0006), which also
    spares the bus 67 events a second under load.

    The rule is "publish when the state actually changed", applied to every
    caller, rather than "pings are special", asserted at the ping handler. The
    difference matters if the machine ever gains a second self-loop: it falls
    under the existing rule instead of needing someone to remember it.

    **A list rather than `Event | None`, and the difference is not cosmetic.**
    An optional invites the one caller who knows it is always `None` to discard
    it, and a discarded return value takes the rule with it: delete the check
    below and that caller's behaviour is unchanged, so no test of it can fail.
    A list is passed to `EventBus.publish` unexamined by every caller, so the
    emptiness has to be produced here to be observed there.
    """
    before = visit.state
    visit.state = advance_visit(before, event)
    if visit.state is before:
        return []
    return [
        VisitTransitioned(
            visit_id=visit.id,
            assignment_id=visit.assignment_id,
            from_state=before,
            to_state=visit.state,
            at=at,
            verdict=verdict,
        )
    ]


def transition_assignment(assignment: Assignment, event: AssignmentEvent, *, at: datetime) -> Event:
    """Advance `assignment` in place and return the event.

    No `None` case: the assignment machine has no self-loops, so every legal move
    is a change worth publishing. Absence of that branch is the claim.
    """
    before = assignment.state
    assignment.state = advance_assignment(before, event)
    return AssignmentTransitioned(
        assignment_id=assignment.id,
        from_state=before,
        to_state=assignment.state,
        at=at,
    )


class EventBus:
    """Fan-out to every connected dashboard: one `asyncio.Queue` per subscriber.

    In-process, and therefore single-process, which is the same deliberate
    limitation the sweeper has and it is recorded once for both (ADR-0006,
    `docs/design.md`). The production answer is a shared bus — Postgres
    `LISTEN/NOTIFY` or Redis — and it is not in this slice.
    """

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[Event]] = set()

    @property
    def subscribers(self) -> int:
        """How many queues are registered right now.

        Exists for the tests that check issue 06's leak: a stream that has ended
        by any route — client gone, consumer cancelled, snapshot failed — must
        leave this at what it was before. Nothing in the serving path reads it.
        """
        return len(self._subscribers)

    @contextmanager
    def subscribe(self) -> Iterator[asyncio.Queue[Event]]:
        """A queue receiving every event published while the block is open.

        A context manager because issue 06's named risk is the leak: a dashboard
        that vanishes without closing cleanly must not hold a queue forever.
        Removal on the way out is not something the SSE handler has to remember —
        its `finally` is this `finally`, and it runs whether the client
        disconnected, the response errored, or the task was cancelled.
        """
        queue: asyncio.Queue[Event] = asyncio.Queue()
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)

    def publish(self, events: Iterable[Event]) -> None:
        """Deliver `events` to every current subscriber. Never blocks, never awaits.

        Synchronous on purpose, in a codebase whose lint config turns a forgotten
        `await` into an error precisely because one in the sweeper "fails silently
        and looks exactly like a working sweeper" (CLAUDE.md). A publish that
        cannot be awaited cannot be un-awaited. `put_nowait` on an unbounded queue
        is the only reason that is possible; a bounded one would put a slow
        dashboard's backlog in the sweeper's path, which is the wrong party to
        punish.

        Iterated into a list first so that a generator argument is not consumed by
        the first subscriber and delivered empty to the second.
        """
        batch = list(events)
        for queue in self._subscribers:
            for event in batch:
                queue.put_nowait(event)
