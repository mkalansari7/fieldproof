"""The two state machines (spec.md §5)."""

import itertools

import pytest

from fieldproof.transitions import (
    AssignmentEvent,
    AssignmentState,
    DeadlinePassed,
    IllegalTransitionError,
    VisitCompleted,
    VisitEvent,
    VisitState,
    advance_assignment,
    advance_visit,
    start_visit,
)
from fieldproof.verification import Verdict


def test_ending_an_active_visit_seals_it_pending_a_report() -> None:
    assert advance_visit(VisitState.ACTIVE, VisitEvent.END) is VisitState.PENDING_REPORT


def test_a_ping_leaves_an_active_visit_active() -> None:
    # The state is unchanged and the machine says so explicitly rather than the
    # caller special-casing ping as "not a transition". `last_ping_at` is the
    # caller's write (spec.md §5); this function has no clock.
    assert advance_visit(VisitState.ACTIVE, VisitEvent.PING) is VisitState.ACTIVE


def test_a_ping_against_a_sealed_visit_is_rejected() -> None:
    # The 409 (spec.md §4, §6). The trail seals on the way out of ACTIVE, so a
    # ping arriving afterwards has nowhere to go: accepting it would append to a
    # sealed trail and change a verdict that has already been computed.
    with pytest.raises(IllegalTransitionError, match="PENDING_REPORT"):
        advance_visit(VisitState.PENDING_REPORT, VisitEvent.PING)


def test_submitting_a_report_completes_a_sealed_visit() -> None:
    # The one edge that triggers verification (spec.md §5). A visit is only ever
    # judged when the participant explicitly ended it *and* reported against it.
    assert (
        advance_visit(VisitState.PENDING_REPORT, VisitEvent.REPORT_SUBMITTED)
        is VisitState.COMPLETED
    )


def test_silence_past_the_abandon_window_abandons_an_active_visit() -> None:
    # The sweeper's edge (spec.md §7). Which clock decided this is the sweeper's
    # business; the machine only knows the event happened.
    assert advance_visit(VisitState.ACTIVE, VisitEvent.SILENCE_ELAPSED) is VisitState.ABANDONED


def test_a_sealed_visit_past_its_report_deadline_goes_unreported() -> None:
    assert (
        advance_visit(VisitState.PENDING_REPORT, VisitEvent.REPORT_DEADLINE_PASSED)
        is VisitState.UNREPORTED
    )


# ------------------------------------------------------- the exhaustive table
#
# Transcribed by hand from spec.md §5, not imported from the implementation. The
# whole value of the table is that it disagrees with the code when the code
# changes; reading the machine's own dict back would make it agree by
# construction and assert nothing.

LEGAL_VISIT_MOVES: dict[tuple[VisitState, VisitEvent], VisitState] = {
    (VisitState.ACTIVE, VisitEvent.PING): VisitState.ACTIVE,
    (VisitState.ACTIVE, VisitEvent.END): VisitState.PENDING_REPORT,
    (VisitState.ACTIVE, VisitEvent.SILENCE_ELAPSED): VisitState.ABANDONED,
    (VisitState.PENDING_REPORT, VisitEvent.REPORT_SUBMITTED): VisitState.COMPLETED,
    (VisitState.PENDING_REPORT, VisitEvent.REPORT_DEADLINE_PASSED): VisitState.UNREPORTED,
}

ALL_VISIT_MOVES = list(itertools.product(VisitState, VisitEvent))


@pytest.mark.parametrize(("current", "event"), ALL_VISIT_MOVES)
def test_every_visit_move_is_either_in_the_table_or_rejected(
    current: VisitState, event: VisitEvent
) -> None:
    # 25 pairs, 5 of them legal. The 20 rejections are what proves the machine is
    # closed, and they are the cheap half to assert.
    expected = LEGAL_VISIT_MOVES.get((current, event))
    if expected is None:
        with pytest.raises(IllegalTransitionError):
            advance_visit(current, event)
    else:
        assert advance_visit(current, event) is expected


# --------------------------------------------------------------- starting one
#
# Start has no from-state (spec.md §5). Its guard reads the assignment and
# whatever visit came before, so it is its own function rather than a row in the
# table above — which is also why ABANDONED -> ACTIVE is unrepresentable here
# rather than merely rejected (ADR-0001).


def test_a_first_visit_against_an_open_assignment_starts_active() -> None:
    assert start_visit(assignment=AssignmentState.ASSIGNED, latest_visit=None) is VisitState.ACTIVE


def test_a_second_visit_while_one_is_still_open_is_rejected() -> None:
    # "At most one non-terminal visit" (ADR-0001). The database enforces this too,
    # with a partial unique index (issue 01) — belt and braces, because the check
    # and the insert are not one statement.
    with pytest.raises(IllegalTransitionError, match="ACTIVE"):
        start_visit(assignment=AssignmentState.ASSIGNED, latest_visit=VisitState.ACTIVE)


def test_a_retry_after_an_abandoned_visit_starts_a_new_active_one() -> None:
    # The whole reason assignment and visit are separate entities (ADR-0001). A
    # participant whose phone died does not lose the assignment: they get a new
    # visit, with its own trail, and the abandoned one stays abandoned. This is
    # emphatically not ABANDONED -> ACTIVE — that edge does not exist.
    assert (
        start_visit(assignment=AssignmentState.ASSIGNED, latest_visit=VisitState.ABANDONED)
        is VisitState.ACTIVE
    )


def test_starting_a_visit_against_an_expired_assignment_is_rejected() -> None:
    # Past the business's deadline there is nothing left to attempt.
    with pytest.raises(IllegalTransitionError, match="EXPIRED"):
        start_visit(assignment=AssignmentState.EXPIRED, latest_visit=None)


ALL_START_CASES = list(itertools.product(AssignmentState, [None, *VisitState]))

STARTABLE = {
    (AssignmentState.ASSIGNED, None),
    (AssignmentState.ASSIGNED, VisitState.COMPLETED),
    (AssignmentState.ASSIGNED, VisitState.ABANDONED),
    (AssignmentState.ASSIGNED, VisitState.UNREPORTED),
}
"""An open assignment with no visit in flight. Transcribed from spec.md §5."""


@pytest.mark.parametrize(("assignment", "latest_visit"), ALL_START_CASES)
def test_a_visit_starts_only_against_an_open_assignment_with_nothing_in_flight(
    assignment: AssignmentState, latest_visit: VisitState | None
) -> None:
    # 18 pairs, 4 startable. ASSIGNED + COMPLETED is deliberately among them: a
    # completed visit fulfils its assignment (ADR-0004), so the assignment would
    # not still read ASSIGNED — the guard against that pair lives in the
    # assignment machine, and duplicating it here would be a second rule to keep
    # in sync with the first.
    if (assignment, latest_visit) in STARTABLE:
        assert start_visit(assignment=assignment, latest_visit=latest_visit) is VisitState.ACTIVE
    else:
        with pytest.raises(IllegalTransitionError):
            start_visit(assignment=assignment, latest_visit=latest_visit)


# --------------------------------------------------------- the assignment side
#
# Two events, one of which carries a verdict it is not allowed to read.


@pytest.mark.parametrize("verdict", list(Verdict))
def test_any_completed_visit_fulfils_its_assignment_whatever_the_verdict(
    verdict: Verdict,
) -> None:
    # ADR-0004, and a deliberate deviation from the brief: the verdict is advice
    # to the business, not a payment gate. A `suspicious` visit still fulfils —
    # whether to pay for it is a judgement with a human behind it, not the output
    # of a threshold someone picked on a Tuesday evening.
    assert (
        advance_assignment(AssignmentState.ASSIGNED, VisitCompleted(verdict))
        is AssignmentState.FULFILLED
    )


def test_an_assignment_past_its_deadline_expires() -> None:
    assert advance_assignment(AssignmentState.ASSIGNED, DeadlinePassed()) is AssignmentState.EXPIRED


def test_completing_an_expired_assignment_is_rejected() -> None:
    # The deadline already closed it. A visit that somehow completes afterwards is
    # a bug upstream — start_visit refuses to open one against EXPIRED — and this
    # is where it surfaces rather than silently reviving the assignment.
    with pytest.raises(IllegalTransitionError, match="EXPIRED"):
        advance_assignment(AssignmentState.EXPIRED, VisitCompleted(Verdict.VERIFIED))


ASSIGNMENT_EVENTS: list[AssignmentEvent] = [VisitCompleted(Verdict.VERIFIED), DeadlinePassed()]
"""One instance per event kind. The verdict is immaterial by ADR-0004, pinned above."""

LEGAL_ASSIGNMENT_MOVES: dict[tuple[AssignmentState, type], AssignmentState] = {
    (AssignmentState.ASSIGNED, VisitCompleted): AssignmentState.FULFILLED,
    (AssignmentState.ASSIGNED, DeadlinePassed): AssignmentState.EXPIRED,
}
"""Transcribed by hand from spec.md §5. EXPIRED and FULFILLED are terminal."""


@pytest.mark.parametrize("current", list(AssignmentState))
@pytest.mark.parametrize("event", ASSIGNMENT_EVENTS)
def test_every_assignment_move_is_either_in_the_table_or_rejected(
    current: AssignmentState, event: AssignmentEvent
) -> None:
    expected = LEGAL_ASSIGNMENT_MOVES.get((current, type(event)))
    if expected is None:
        with pytest.raises(IllegalTransitionError):
            advance_assignment(current, event)
    else:
        assert advance_assignment(current, event) is expected


@pytest.mark.parametrize(
    "terminal", [VisitState.COMPLETED, VisitState.ABANDONED, VisitState.UNREPORTED]
)
@pytest.mark.parametrize("event", list(VisitEvent))
def test_a_terminal_visit_accepts_nothing_at_all(terminal: VisitState, event: VisitEvent) -> None:
    # Stated on its own rather than left implicit in the big table, because it is
    # the property ADR-0001 turns on: no grace window, no un-abandoning, no
    # reporting against a visit that timed out. A participant recovers by starting
    # a new visit, and `start_visit` is the only door.
    with pytest.raises(IllegalTransitionError):
        advance_visit(terminal, event)
