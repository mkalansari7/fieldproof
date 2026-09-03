"""The two state machines: what may happen to an assignment and to a visit.

Pure, like verification: states and events in, a state out. No clock, no
database. The sweeper's transitions and the API handlers' transitions run the
same functions, so the dashboard cannot tell which path produced an event
(spec.md §7).
"""

from dataclasses import dataclass
from enum import Enum

from fieldproof.verification import Verdict


class AssignmentState(Enum):
    """A task a business issued (CONTEXT.md). Not an attempt at it — that is a visit."""

    ASSIGNED = "ASSIGNED"
    EXPIRED = "EXPIRED"
    FULFILLED = "FULFILLED"


class VisitState(Enum):
    """One attempt at an assignment (CONTEXT.md)."""

    ACTIVE = "ACTIVE"
    PENDING_REPORT = "PENDING_REPORT"
    COMPLETED = "COMPLETED"
    ABANDONED = "ABANDONED"
    UNREPORTED = "UNREPORTED"


class VisitEvent(Enum):
    """What can happen to a visit that already exists (spec.md §5)."""

    PING = "ping"
    END = "end"
    SILENCE_ELAPSED = "silence past the abandon window"
    REPORT_SUBMITTED = "report submitted"
    REPORT_DEADLINE_PASSED = "report deadline passed"


class IllegalTransitionError(ValueError):
    """A move the machine does not have. The API layer answers these with 409."""


_VISIT_TABLE: dict[tuple[VisitState, VisitEvent], VisitState] = {
    (VisitState.ACTIVE, VisitEvent.PING): VisitState.ACTIVE,
    (VisitState.ACTIVE, VisitEvent.END): VisitState.PENDING_REPORT,
    (VisitState.ACTIVE, VisitEvent.SILENCE_ELAPSED): VisitState.ABANDONED,
    (VisitState.PENDING_REPORT, VisitEvent.REPORT_SUBMITTED): VisitState.COMPLETED,
    (VisitState.PENDING_REPORT, VisitEvent.REPORT_DEADLINE_PASSED): VisitState.UNREPORTED,
}
"""Every legal visit move (spec.md §5). Absence from this dict is the rejection.

Written as data rather than branches because the closure property is the point:
`COMPLETED`, `ABANDONED` and `UNREPORTED` appear only as values, never as keys,
so the fifteen moves out of a terminal state are absent rather than guarded
against. There is no `if` that has to remember not to write them.
"""


def advance_visit(current: VisitState, event: VisitEvent) -> VisitState:
    """Advance one visit (spec.md §5). Raises on any move not in the table."""
    try:
        return _VISIT_TABLE[current, event]
    except KeyError:
        raise IllegalTransitionError(
            f"a visit in {current.value} has no {event.value} transition"
        ) from None


NON_TERMINAL_VISIT_STATES = frozenset({VisitState.ACTIVE, VisitState.PENDING_REPORT})
"""The visit states that still expect something to happen (CONTEXT.md).

An assignment may have many visits and at most one in this set (ADR-0001). Named
rather than inlined because issue 01's partial unique index has to agree with it,
and the sweeper scans on the same distinction.
"""


def start_visit(*, assignment: AssignmentState, latest_visit: VisitState | None) -> VisitState:
    """Open a new visit against an assignment (spec.md §5). Raises if it may not.

    `latest_visit` is the most recent visit's state, or `None` when the
    participant has not attempted this assignment before. A terminal one is no
    obstacle: retrying is how a participant recovers from a dead phone, and the
    new visit carries its own trail (ADR-0001). This is not a transition *out of*
    that terminal state, which is why there is no START event in `VisitEvent`:
    resurrection is unrepresentable rather than merely rejected.
    """
    if assignment is not AssignmentState.ASSIGNED:
        raise IllegalTransitionError(f"an assignment in {assignment.value} cannot be attempted")
    if latest_visit is not None and latest_visit in NON_TERMINAL_VISIT_STATES:
        raise IllegalTransitionError(f"this assignment already has a visit in {latest_visit.value}")
    # Every terminal `latest_visit` falls through, `COMPLETED` included. That one
    # pairing is unreachable rather than permitted: a completed visit fulfils its
    # assignment in the same stroke (ADR-0004), so an `ASSIGNED` assignment cannot
    # still be looking at one. Guarded by the assignment machine, not duplicated
    # here — two copies of a rule are two things to keep in step.
    return VisitState.ACTIVE


@dataclass(frozen=True)
class VisitCompleted:
    """A visit reached COMPLETED. Carries the verdict, and the machine ignores it.

    The verdict is here on purpose. `advance_assignment` has the information in
    hand and declines to branch on it (ADR-0004), which is a claim a test can
    actually falsify — an enum member with no payload would make the same rule
    true by construction and assert nothing. It also puts the deviation from the
    brief at the seam where a reader will look for it.
    """

    verdict: Verdict


@dataclass(frozen=True)
class DeadlinePassed:
    """The business's deadline elapsed with the assignment still open (spec.md §5, §7).

    `deadline_at` is a *start-by* time: this event is raised only for an
    assignment with no visit in flight. That clause is the sweep's, not this
    machine's — `advance_assignment` sees one row and cannot ask about others —
    so `sweeper.expire_overdue_assignments` is where it is enforced and tested.
    """


type AssignmentEvent = VisitCompleted | DeadlinePassed
"""What can happen to an assignment (spec.md §5).

A union of records rather than an `Enum` like `VisitEvent`, because one of the two
carries data. A third variant added here and nowhere else falls through to
rejection rather than to a silently wrong state — closed by default, the same
property the visit table gets from being a dict.
"""


def advance_assignment(current: AssignmentState, event: AssignmentEvent) -> AssignmentState:
    """Advance one assignment (spec.md §5). Raises on any move not in the table."""
    if current is AssignmentState.ASSIGNED:
        match event:
            case VisitCompleted():
                return AssignmentState.FULFILLED
            case DeadlinePassed():
                return AssignmentState.EXPIRED
    raise IllegalTransitionError(
        f"an assignment in {current.value} has no {type(event).__name__} transition"
    )
