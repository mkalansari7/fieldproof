"""Verification: the pure computation from a sealed ping trail to a verdict.

No clock, no database, no I/O (ADR-0002). Callers pass values in.
"""

import itertools
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class Classification(Enum):
    """Where a ping's uncertainty interval falls relative to the target radius."""

    INSIDE = "INSIDE"
    OUTSIDE = "OUTSIDE"
    INCONCLUSIVE = "INCONCLUSIVE"


def classify(*, distance_m: float, accuracy_m: float, radius_m: float) -> Classification:
    """Classify one ping by its uncertainty interval (spec.md §3, ADR-0003)."""
    if distance_m + accuracy_m < radius_m:
        return Classification.INSIDE
    if distance_m - accuracy_m > radius_m:
        return Classification.OUTSIDE
    return Classification.INCONCLUSIVE


class Verdict(Enum):
    """The outcome of verification for a visit. Never a boolean."""

    VERIFIED = "verified"
    SUSPICIOUS = "suspicious"
    UNVERIFIABLE = "unverifiable"


@dataclass(frozen=True)
class Ping:
    """One reported position, as verification sees it.

    `distance_m` is computed once at ingest (ADR-0002). Classification is *not*
    carried here: it depends on `radius_m`, and re-scoring a historical visit
    under a different radius has to be able to reach a different answer.
    """

    received_at: datetime
    distance_m: float
    accuracy_m: float


@dataclass(frozen=True)
class AssignmentTerms:
    """The venue and task facts that legitimately vary per assignment (spec.md §1).

    Separate from `ScoringConfig` deliberately: a kiosk and a shopping mall
    genuinely differ, whereas the judgement thresholds do not (ADR-0002).
    """

    radius_m: float
    min_duration_s: int


@dataclass(frozen=True)
class ScoringConfig:
    """The versioned judgement thresholds (spec.md §1).

    Changing any of these is a code change with a git history, not a row edit.
    `version` is stamped onto every verdict so a stored result traces to the
    rules that produced it.
    """

    sufficiency_s: int = 180
    dwell_ratio_min: float = 0.80
    gap_attribution_limit_s: int = 60
    version: str = "v1"


class IncoherentTermsError(ValueError):
    """Assignment terms under which no visit could ever be verified."""


def check_terms(terms: AssignmentTerms, config: ScoringConfig) -> None:
    """Reject assignment terms that make `VERIFIED` unreachable. Raises.

    Attributed time is measured over the visit, so it can never exceed
    `visit_duration_s`. A `min_duration_s` below `config.sufficiency_s` therefore
    admits visits that clear the duration gate and still cannot reach the
    sufficiency one — a flawless visit under such terms is `UNVERIFIABLE`, and
    the business sees an honest participant it can never verify. The failure is
    invisible in the defaults, where `min_duration_s` is 300 against a 180s
    threshold; it appears the first time someone writes a two-minute task.

    Call this where an assignment is created, **not** from `verify`. Scoring has
    to stay total over stored data: raising here would turn a later
    `SUFFICIENCY_S` increase into an outage across every historical visit whose
    assignment predates it, and replaying history under a newer config is the
    property ADR-0002 exists to protect.
    """
    if terms.min_duration_s < config.sufficiency_s:
        raise IncoherentTermsError(
            f"min_duration_s {terms.min_duration_s} is below the scoring config's "
            f"sufficiency_s {config.sufficiency_s}: no visit under these terms could "
            f"accumulate enough attributed time to be verified."
        )


@dataclass(frozen=True)
class Verification:
    """A verdict and the breakdown that produced it.

    The dashboard renders the breakdown and the debrief depends on it, so it is
    returned in full rather than reduced to the bucket.
    """

    verdict: Verdict
    inside_s: float
    outside_s: float
    unattributed_s: float
    attributed_total_s: float
    dwell_ratio: float
    conclusive_pings: int
    total_pings: int
    visit_duration_s: float
    radius_m: float
    min_duration_s: int
    scoring_config_version: str


def verify(
    *,
    ping_trail: Sequence[Ping],
    terms: AssignmentTerms,
    visit_duration_s: float,
    config: ScoringConfig,
) -> Verification:
    """Judge one sealed ping trail (spec.md §3).

    `visit_duration_s` is `ended_at - started_at` on the server clock and is
    passed in, never derived from the trail: the trail is empty in exactly the
    case where this value decides the verdict.

    **Caller invariant: every ping falls within the visit window.** Attributed
    time is summed from `received_at` deltas while `unattributed_s` is the
    remainder of `visit_duration_s`, so a trail carrying pings received after
    `ended_at` can push `attributed_total_s` past the visit duration and render
    a negative `unattributed_s` on the dashboard. Enforcing it here is the wrong
    place — this function cannot distinguish a late write from a caller passing
    the wrong duration, and clamping would bury the discrepancy it should be
    surfacing. It holds by construction: a ping against a non-`ACTIVE` visit is
    409 (spec.md §4) and the trail seals on the way out of `ACTIVE` (§5). What
    it rests on is that the state check and the ping write are not atomic across
    an await, so the trail query that feeds this function is where the window
    gets enforced.
    """
    # received_at is the sole basis for ordering (spec.md §4); a caller replaying
    # stored rows does not owe us a sort. Inconclusive pings drop out here rather
    # than breaking the chain: they are skipped, not treated as a gap.
    conclusive: list[tuple[Ping, Classification]] = []
    for ping in sorted(ping_trail, key=lambda ping: ping.received_at):
        classification = classify(
            distance_m=ping.distance_m,
            accuracy_m=ping.accuracy_m,
            radius_m=terms.radius_m,
        )
        if classification is not Classification.INCONCLUSIVE:
            conclusive.append((ping, classification))

    inside_s = 0.0
    outside_s = 0.0
    for (earlier, earlier_class), (later, later_class) in itertools.pairwise(conclusive):
        gap_s = (later.received_at - earlier.received_at).total_seconds()
        # A pair that disagrees contributes nothing, deliberately. The obvious
        # alternative is to split a transition interval — half inside, half out,
        # or pro-rated by distance. We decline: the interval covers a crossing
        # whose moment we do not observe, so any split is a number we invented
        # sitting in a field the business reads as measured. Claiming nothing is
        # the same rule the pocketed phone gets, and it errs toward the
        # participant, since unattributed time cannot pull dwell_ratio down.
        if gap_s > config.gap_attribution_limit_s or earlier_class is not later_class:
            continue
        if earlier_class is Classification.INSIDE:
            inside_s += gap_s
        else:
            outside_s += gap_s

    attributed_total_s = inside_s + outside_s
    dwell_ratio = inside_s / attributed_total_s if attributed_total_s else 0.0

    # Order matters (spec.md §3). Duration runs first and is the only gate that
    # does not read the trail: it rests on the server clock, which no pocketed
    # phone, denied permission or indoor accuracy can shorten.
    if visit_duration_s < terms.min_duration_s:
        verdict = Verdict.SUSPICIOUS
    elif attributed_total_s < config.sufficiency_s:
        verdict = Verdict.UNVERIFIABLE
    elif dwell_ratio >= config.dwell_ratio_min:
        verdict = Verdict.VERIFIED
    else:
        verdict = Verdict.SUSPICIOUS

    return Verification(
        verdict=verdict,
        inside_s=inside_s,
        outside_s=outside_s,
        unattributed_s=visit_duration_s - attributed_total_s,
        attributed_total_s=attributed_total_s,
        dwell_ratio=dwell_ratio,
        conclusive_pings=len(conclusive),
        total_pings=len(ping_trail),
        visit_duration_s=visit_duration_s,
        radius_m=terms.radius_m,
        min_duration_s=terms.min_duration_s,
        scoring_config_version=config.version,
    )
