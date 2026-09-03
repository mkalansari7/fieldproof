"""The tables (spec.md §2).

Five of them: `assignment`, `visit`, `ping`, `report`, `verdict`. The enums are
the ones the pure modules already define, so a state that the transition table
does not have is a state the database will not accept either.

Two rules are enforced here rather than in application code, because application
code cannot enforce them across concurrent requests:

- at most one non-terminal visit per assignment (ADR-0001), as a partial unique
  index; and
- one report and one verdict per visit, as unique foreign keys.

A third is deliberately *not* enforced here: nothing stops a row being written in
a state the transition table would refuse, because legality depends on other rows
and is `transitions`' job. `Assignment` gets a creation seam (`new_assignment`)
only because its guard, `check_terms`, needs nothing but the row itself. `Visit`
has no equivalent, and the reasoning is on `Visit` itself.

Timestamps are `timestamptz` throughout. The distinction between `reported_at`
and `received_at` (spec.md §4) is a column split from the start: merging them
later would be a migration and a rewrite of every scoring test.
"""

from datetime import datetime
from enum import Enum as PyEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from fieldproof.config import DEFAULT_MIN_DURATION_S, DEFAULT_RADIUS_M, SCORING_CONFIG
from fieldproof.transitions import NON_TERMINAL_VISIT_STATES, AssignmentState, VisitState
from fieldproof.verification import (
    AssignmentTerms,
    Classification,
    ScoringConfig,
    Verdict,
    check_terms,
)


class Base(DeclarativeBase):
    """Declarative base. `Base.metadata` is the schema `database.create_schema` builds."""


def _enum(python_enum: type[PyEnum], name: str) -> Enum:
    """A native Postgres enum storing the member *values*, not their Python names.

    The default stores names, which would put `VERIFIED` in a column spec.md §2
    spells `verified`. The values are the spelling the spec, the API payloads and
    every hand-written query in the writeup use, so they are the spelling on disk.
    """
    return Enum(
        python_enum,
        name=name,
        values_callable=lambda enum_cls: [member.value for member in enum_cls],
    )


_Timestamp = DateTime(timezone=True)
"""`timestamptz`. Never `timestamp`: `received_at` and `reported_at` are compared
against each other and against a server clock, and a naive column here would make
that comparison quietly wrong (CLAUDE.md)."""


class Assignment(Base):
    """A task issued by a business, bound to a target location and a deadline.

    Construct through `new_assignment`, which applies the terms guard.
    """

    __tablename__ = "assignment"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    business_name: Mapped[str] = mapped_column(String(200))
    participant_name: Mapped[str] = mapped_column(String(200))

    target_lat: Mapped[float] = mapped_column(Float)
    target_lng: Mapped[float] = mapped_column(Float)
    radius_m: Mapped[float] = mapped_column(Float, default=DEFAULT_RADIUS_M)
    min_duration_s: Mapped[int] = mapped_column(Integer, default=DEFAULT_MIN_DURATION_S)

    deadline_at: Mapped[datetime] = mapped_column(_Timestamp)
    state: Mapped[AssignmentState] = mapped_column(
        _enum(AssignmentState, "assignment_state"), default=AssignmentState.ASSIGNED
    )
    created_at: Mapped[datetime] = mapped_column(_Timestamp)

    __table_args__ = (
        # The sweeper's scan: ASSIGNED rows past their deadline, every SWEEP_TICK_S.
        Index("ix_assignment_state_deadline_at", "state", "deadline_at"),
    )


def new_assignment(
    *,
    business_name: str,
    participant_name: str,
    target_lat: float,
    target_lng: float,
    deadline_at: datetime,
    created_at: datetime,
    radius_m: float = DEFAULT_RADIUS_M,
    min_duration_s: int = DEFAULT_MIN_DURATION_S,
    id: UUID | None = None,
    config: ScoringConfig = SCORING_CONFIG,
) -> Assignment:
    """Build an assignment, refusing terms under which no visit could ever verify.

    This is the single creation seam, and it exists so that `check_terms`
    (spec.md §1) has exactly one place to be called from. Putting the guard on
    the constructor rather than at each call site is what keeps a future API
    handler from being the one path that forgets it.

    `created_at` and `deadline_at` are passed in rather than defaulted from a
    clock, matching the pure modules: the seed's deadlines are relative to a `now`
    it chooses, and a hidden `datetime.now()` here would make them untestable.
    """
    check_terms(AssignmentTerms(radius_m=radius_m, min_duration_s=min_duration_s), config)
    return Assignment(
        id=id if id is not None else uuid4(),
        business_name=business_name,
        participant_name=participant_name,
        target_lat=target_lat,
        target_lng=target_lng,
        radius_m=radius_m,
        min_duration_s=min_duration_s,
        deadline_at=deadline_at,
        state=AssignmentState.ASSIGNED,
        created_at=created_at,
    )


_NON_TERMINAL_PREDICATE = "state IN ({})".format(
    ", ".join(
        f"'{state.value}'"
        for state in sorted(NON_TERMINAL_VISIT_STATES, key=lambda state: state.value)
    )
)
"""Built from `transitions.NON_TERMINAL_VISIT_STATES` rather than restated.

The frozenset is named over there precisely so this index can agree with it: a
state added to the machine and not to this predicate would let two live visits
exist against one assignment, which is the one thing ADR-0001 forbids and the
one thing application code cannot reliably prevent under concurrency.
"""


class Visit(Base):
    """One attempt by a participant to perform an assignment (CONTEXT.md).

    **There is deliberately no `new_visit` seam beside `new_assignment`, and
    `state` has no default.** `Visit(state=VisitState.COMPLETED, ...)` is
    therefore legal, which sits oddly next to `transitions.start_visit`'s claim
    that resurrection is unrepresentable: that claim is about the *machine*, and
    a row constructor is not the machine.

    The reason is that a visit's legal openings depend on rows this class cannot
    see — the assignment's state and the latest visit's state, which is a query.
    A constructor here could only re-ask a question `start_visit` already answers,
    and two copies of that rule would be two things to keep in step. What stops a
    bad row reaching the table is the partial unique index below, which does not
    care which code path proposed it.

    Writers go through `transitions` first and construct a row second. The seed
    and the tests construct rows directly on purpose: they are placing fixtures,
    not making moves, and a fixture that had to walk the machine to reach
    `PENDING_REPORT` would be testing the machine instead of the schema.
    """

    __tablename__ = "visit"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    assignment_id: Mapped[UUID] = mapped_column(ForeignKey("assignment.id"))
    state: Mapped[VisitState] = mapped_column(_enum(VisitState, "visit_state"))

    started_at: Mapped[datetime] = mapped_column(_Timestamp)
    ended_at: Mapped[datetime | None] = mapped_column(_Timestamp, default=None)
    last_ping_at: Mapped[datetime] = mapped_column(_Timestamp)
    """NOT NULL, initialised to `started_at` by whoever opens the visit.

    A nullable column would need the sweeper to write `last_ping_at IS NULL OR
    last_ping_at < ...`, and the null branch has no honest answer: a visit that
    has never pinged is not a visit that pinged long ago, but spec.md §5 abandons
    on silence, and silence is exactly what it has. Seeding it from `started_at`
    makes the sweeper's predicate total.

    The consequence is deliberate: a permission-denied visit (spec.md §8 starts
    it anyway) is `ABANDONED` after `ABANDON_AFTER_S` if the participant never
    ends it. That is the same rule every silent visit gets, and the server cannot
    tell the two apart. Ending it inside the window still reaches `COMPLETED` and
    scores `unverifiable` on zero pings, which is §3's designed outcome.
    """

    report_deadline_at: Mapped[datetime | None] = mapped_column(_Timestamp, default=None)
    created_at: Mapped[datetime] = mapped_column(_Timestamp)

    __table_args__ = (
        # The sweeper's ACTIVE scan: rows whose last_ping_at is older than
        # ABANDON_AFTER_S. Its PENDING_REPORT scan gets only the `state` prefix
        # out of this index and then filters on report_deadline_at, which is not
        # in it — spec.md §2 names this index and only this one, and PENDING_REPORT
        # is a handful of rows. Revisit if that stops being true.
        Index("ix_visit_state_last_ping_at", "state", "last_ping_at"),
        Index(
            "ux_visit_one_non_terminal_per_assignment",
            "assignment_id",
            unique=True,
            postgresql_where=text(_NON_TERMINAL_PREDICATE),
        ),
    )


class Ping(Base):
    """One reported position during an active visit (CONTEXT.md).

    `distance_m` is computed once at write (ADR-0002), and it is the column doing
    that work: it turns verification into an aggregate over stored rows rather
    than a trail walk that redoes geodesy.

    `classification` is not. `verify` re-derives it from `distance_m` and
    `accuracy_m`, because the answer depends on `radius_m` and re-scoring at a
    different radius has to be able to reach a different one. The column is
    write-only as far as judgement is concerned — spec.md §2 mandates it, and it
    earns its place for the dashboard and for auditing what the radius was at the
    time, not for scoring.
    """

    __tablename__ = "ping"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    visit_id: Mapped[UUID] = mapped_column(ForeignKey("visit.id"))

    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    accuracy_m: Mapped[float] = mapped_column(Float)

    reported_at: Mapped[datetime] = mapped_column(_Timestamp)
    """The client's clock. Stored as a tamper signal. **Never scored on** (spec.md §4)."""

    received_at: Mapped[datetime] = mapped_column(_Timestamp)
    """The server's clock. The sole basis for ordering and scoring (spec.md §4)."""

    distance_m: Mapped[float] = mapped_column(Float)
    classification: Mapped[Classification] = mapped_column(
        _enum(Classification, "ping_classification")
    )

    __table_args__ = (
        # Verification reads one visit's whole trail in received_at order.
        Index("ix_ping_visit_id_received_at", "visit_id", "received_at"),
    )


class Report(Base):
    """The participant's written account, submitted after the visit is sealed."""

    __tablename__ = "report"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    visit_id: Mapped[UUID] = mapped_column(ForeignKey("visit.id"), unique=True)
    body: Mapped[str] = mapped_column(Text)
    submitted_at: Mapped[datetime] = mapped_column(_Timestamp)


class VerdictRecord(Base):
    """The stored outcome of verification for one visit.

    Named `VerdictRecord` so that `Verdict` keeps meaning the three-valued enum it
    means everywhere else; the table is `verdict`, as spec.md §2 has it. This is
    the persisted form of `verification.Verification`.

    It holds the full breakdown rather than the bucket alone, for two reasons: the
    dashboard renders the breakdown without recomputing anything, and `radius_m`,
    `min_duration_s` and `scoring_config_version` are snapshotted so a stored
    result traces to the rules that produced it (ADR-0002).
    """

    __tablename__ = "verdict"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    visit_id: Mapped[UUID] = mapped_column(ForeignKey("visit.id"), unique=True)

    verdict: Mapped[Verdict] = mapped_column(_enum(Verdict, "verdict_outcome"))

    inside_s: Mapped[float] = mapped_column(Float)
    outside_s: Mapped[float] = mapped_column(Float)
    unattributed_s: Mapped[float] = mapped_column(Float)
    attributed_total_s: Mapped[float] = mapped_column(Float)
    dwell_ratio: Mapped[float] = mapped_column(Float)

    conclusive_pings: Mapped[int] = mapped_column(Integer)
    total_pings: Mapped[int] = mapped_column(Integer)
    visit_duration_s: Mapped[float] = mapped_column(Float)

    radius_m: Mapped[float] = mapped_column(Float)
    min_duration_s: Mapped[int] = mapped_column(Integer)
    scoring_config_version: Mapped[str] = mapped_column(String(16))
    computed_at: Mapped[datetime] = mapped_column(_Timestamp)
