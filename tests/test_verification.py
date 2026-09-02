"""Verification: the pure function (spec.md §3)."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest

from fieldproof.verification import (
    AssignmentTerms,
    Classification,
    IncoherentTermsError,
    Ping,
    ScoringConfig,
    Verdict,
    check_terms,
    classify,
    verify,
)


def test_ping_whose_uncertainty_interval_falls_wholly_inside_the_radius_is_inside() -> None:
    assert classify(distance_m=40.0, accuracy_m=30.0, radius_m=100.0) is Classification.INSIDE


def test_ping_whose_uncertainty_interval_falls_wholly_outside_the_radius_is_outside() -> None:
    assert classify(distance_m=500.0, accuracy_m=30.0, radius_m=100.0) is Classification.OUTSIDE


def test_ping_whose_uncertainty_interval_straddles_the_radius_is_inconclusive() -> None:
    assert classify(distance_m=95.0, accuracy_m=30.0, radius_m=100.0) is Classification.INCONCLUSIVE


def test_junk_accuracy_dead_on_target_is_inconclusive_without_an_accuracy_cutoff() -> None:
    # ADR-0003: accuracy 800 against a 100m radius is already inconclusive. The
    # interval arithmetic absorbs junk data, so no cutoff rule is needed.
    assert classify(distance_m=0.0, accuracy_m=800.0, radius_m=100.0) is Classification.INCONCLUSIVE


def test_fake_precision_far_from_target_is_outside_not_inside() -> None:
    # ADR-0003: self-reported precision earns nothing. accuracy 3 at 500m is a
    # conclusive OUTSIDE, exactly as an honest ping at that distance would be.
    assert classify(distance_m=500.0, accuracy_m=3.0, radius_m=100.0) is Classification.OUTSIDE


# -------------------------------------------------------------------- verify
#
# All cases assume radius_m 100 and min_duration_s 300 unless stated (issue 02).

TERMS = AssignmentTerms(radius_m=100.0, min_duration_s=300)
CONFIG = ScoringConfig()

VISIT_START = datetime(2026, 9, 2, 10, 0, 0, tzinfo=UTC)


def at(offset_s: float) -> datetime:
    """A server receipt time, `offset_s` into the visit."""
    return VISIT_START + timedelta(seconds=offset_s)


def test_zero_pings_on_a_visit_long_enough_for_the_task_is_unverifiable() -> None:
    # Permission denied, 600s visit. Duration passes, then attributed_total is 0,
    # which is below SUFFICIENCY_S. No special case for the empty trail.
    verification = verify(ping_trail=[], terms=TERMS, visit_duration_s=600.0, config=CONFIG)

    assert verification.verdict is Verdict.UNVERIFIABLE
    assert verification.inside_s == 0.0
    assert verification.outside_s == 0.0
    assert verification.attributed_total_s == 0.0
    assert verification.unattributed_s == 600.0
    assert verification.dwell_ratio == 0.0
    assert verification.conclusive_pings == 0
    assert verification.total_pings == 0


def pings(offsets_s: Sequence[float], *, distance_m: float, accuracy_m: float) -> list[Ping]:
    """A ping trail at the given offsets into the visit, all equally placed."""
    return [
        Ping(received_at=at(offset), distance_m=distance_m, accuracy_m=accuracy_m)
        for offset in offsets_s
    ]


def test_a_short_sprint_through_entirely_inside_the_radius_is_suspicious() -> None:
    # Reversed on 2026-09-02 (docs/ai-log.md): this used to read `unverifiable`.
    # Duration runs first, so a 90s all-inside visit against a 300s task is
    # evidence rather than an absence of it. Under sufficiency-first the 90s of
    # attributed time falls below SUFFICIENCY_S and this case never reaches the
    # duration check at all — which makes sprinting the cheapest way to launder
    # a visit that was never performed.
    verification = verify(
        ping_trail=pings(range(0, 91, 15), distance_m=10.0, accuracy_m=20.0),
        terms=TERMS,
        visit_duration_s=90.0,
        config=CONFIG,
    )

    assert verification.verdict is Verdict.SUSPICIOUS
    assert verification.inside_s == 90.0
    assert verification.conclusive_pings == 7
    assert verification.total_pings == 7


def test_zero_pings_on_a_visit_too_short_for_the_task_is_suspicious() -> None:
    # The counter-case, accepted rather than argued away (docs/ai-log.md): under
    # sufficiency-first this was `unverifiable`. There is no innocent account of
    # a 300s task performed inside a 60s visit, and the verdict rests on the
    # server clock rather than on the missing pings. False starts cost nothing —
    # an abandoned visit never reaches verification at all (spec.md §5).
    verification = verify(ping_trail=[], terms=TERMS, visit_duration_s=60.0, config=CONFIG)

    assert verification.verdict is Verdict.SUSPICIOUS
    assert verification.attributed_total_s == 0.0


def test_good_evidence_does_not_buy_back_missing_time() -> None:
    # 200s visit, 190s of it attributed conclusively inside. Suspicious via
    # min_duration_s, not verified: the participant was demonstrably there, and
    # demonstrably not there long enough to have done the task.
    verification = verify(
        ping_trail=pings(range(0, 191, 10), distance_m=10.0, accuracy_m=20.0),
        terms=TERMS,
        visit_duration_s=200.0,
        config=CONFIG,
    )

    assert verification.verdict is Verdict.SUSPICIOUS
    assert verification.inside_s == 190.0
    assert verification.dwell_ratio == 1.0


CLEAN_TRAIL = pings(range(0, 286, 15), distance_m=10.0, accuracy_m=20.0)
"""285s of conclusively-inside attributed time. Clears sufficiency and dwell."""


@pytest.mark.parametrize(
    ("visit_duration_s", "expected"),
    [
        (299.0, Verdict.SUSPICIOUS),
        (300.0, Verdict.VERIFIED),
    ],
)
def test_the_minimum_duration_boundary(visit_duration_s: float, expected: Verdict) -> None:
    # Identical clean trails either side of min_duration_s. The gate is `<`, so
    # a visit exactly as long as the task asks for passes it.
    verification = verify(
        ping_trail=CLEAN_TRAIL,
        terms=TERMS,
        visit_duration_s=visit_duration_s,
        config=CONFIG,
    )

    assert verification.verdict is expected
    assert verification.inside_s == 285.0
    assert verification.dwell_ratio == 1.0


# ------------------------------------------------------- sufficiency gate
#
# Runs second, on visits already long enough for the task.


def test_junk_accuracy_throughout_a_long_visit_is_unverifiable() -> None:
    # Every ping accuracy 800, dead on target, 600s visit. ADR-0003: the interval
    # arithmetic absorbs junk with no cutoff rule, so every ping is inconclusive
    # and there is nothing to judge. Hardware failure is not misconduct.
    verification = verify(
        ping_trail=pings(range(0, 601, 15), distance_m=0.0, accuracy_m=800.0),
        terms=TERMS,
        visit_duration_s=600.0,
        config=CONFIG,
    )

    assert verification.verdict is Verdict.UNVERIFIABLE
    assert verification.conclusive_pings == 0
    assert verification.total_pings == 41
    assert verification.attributed_total_s == 0.0


def test_a_pocket_gap_mid_visit_is_unattributed_rather_than_punished() -> None:
    # 20-minute gap inside a 30-minute visit, good pings either side. The iOS
    # suspension measurement (docs/design.md) says this is the normal case, not
    # the adversarial one. Absence of evidence is not evidence.
    before = pings([0, 60, 120, 180, 240, 300], distance_m=10.0, accuracy_m=20.0)
    after = pings([1500, 1560, 1620, 1680, 1740, 1800], distance_m=10.0, accuracy_m=20.0)

    verification = verify(
        ping_trail=[*before, *after],
        terms=TERMS,
        visit_duration_s=1800.0,
        config=CONFIG,
    )

    assert verification.verdict is Verdict.VERIFIED
    assert verification.inside_s == 600.0
    assert verification.outside_s == 0.0
    assert verification.unattributed_s == 1200.0
    assert verification.dwell_ratio == 1.0


@pytest.mark.parametrize(
    ("last_offset_s", "expected_attributed_s", "expected"),
    [
        (179.0, 179.0, Verdict.UNVERIFIABLE),
        (180.0, 180.0, Verdict.VERIFIED),
    ],
)
def test_the_sufficiency_boundary(
    last_offset_s: float, expected_attributed_s: float, expected: Verdict
) -> None:
    # A 600s visit clears the duration gate either way, so SUFFICIENCY_S alone
    # separates these. The gate is `<`: exactly 180s of accountable time is
    # enough to judge on.
    verification = verify(
        ping_trail=pings([0, 60, 120, last_offset_s], distance_m=10.0, accuracy_m=20.0),
        terms=TERMS,
        visit_duration_s=600.0,
        config=CONFIG,
    )

    assert verification.verdict is expected
    assert verification.attributed_total_s == expected_attributed_s


# -------------------------------------------------------------- dwell gate
#
# Runs last, on visits long enough for the task with enough accountable time.


@pytest.mark.parametrize(
    ("distance_m", "accuracy_m", "description"),
    [
        (2_000.0, 5.0, "a perfect-accuracy trail 2km from the target"),
        (500.0, 3.0, "fake precision — accuracy 3 at 500m earns nothing (ADR-0003)"),
    ],
)
def test_accountable_time_spent_outside_the_radius_is_suspicious(
    distance_m: float, accuracy_m: float, description: str
) -> None:
    verification = verify(
        ping_trail=pings(range(0, 601, 60), distance_m=distance_m, accuracy_m=accuracy_m),
        terms=TERMS,
        visit_duration_s=600.0,
        config=CONFIG,
    )

    assert verification.verdict is Verdict.SUSPICIOUS, description
    assert verification.outside_s == 600.0
    assert verification.inside_s == 0.0
    assert verification.dwell_ratio == 0.0


@pytest.mark.parametrize(
    ("inside_offsets_s", "expected_ratio", "expected"),
    [
        ([0, 60, 120, 180, 237], 0.79, Verdict.SUSPICIOUS),
        ([0, 60, 120, 180, 240], 0.80, Verdict.VERIFIED),
    ],
)
def test_the_dwell_ratio_boundary(
    inside_offsets_s: list[float], expected_ratio: float, expected: Verdict
) -> None:
    # A 600s visit with a fixed 63s / 60s outside tail, varying only the inside
    # time so attributed_total stays 300s and the ratio lands either side of
    # DWELL_RATIO_MIN. The gate is `>=`: exactly 0.80 is verified.
    outside_offsets_s = [300, 360, 363] if expected_ratio == 0.79 else [300, 360]
    verification = verify(
        ping_trail=[
            *pings(inside_offsets_s, distance_m=10.0, accuracy_m=20.0),
            *pings(outside_offsets_s, distance_m=2_000.0, accuracy_m=20.0),
        ],
        terms=TERMS,
        visit_duration_s=600.0,
        config=CONFIG,
    )

    assert verification.attributed_total_s == 300.0
    assert verification.dwell_ratio == pytest.approx(expected_ratio)
    assert verification.verdict is expected


# ------------------------------------------------------------- attribution
#
# Time is attributed only between adjacent *agreeing conclusive* pings close
# enough together to assume continuity. Everything else counts for neither side.


def test_inconclusive_pings_are_skipped_not_treated_as_a_break() -> None:
    # A single indoor ping between two clean ones does not sever continuity: it
    # is skipped, and the surrounding conclusive pair is still adjacent. If it
    # broke the chain, walking indoors would cost a participant their evidence —
    # and indoors is where mystery shopping happens (ADR-0003).
    trail = [
        *pings([0], distance_m=10.0, accuracy_m=20.0),
        *pings([30], distance_m=95.0, accuracy_m=30.0),  # straddles the radius
        *pings([60], distance_m=10.0, accuracy_m=20.0),
    ]

    verification = verify(ping_trail=trail, terms=TERMS, visit_duration_s=600.0, config=CONFIG)

    assert verification.total_pings == 3
    assert verification.conclusive_pings == 2
    assert verification.inside_s == 60.0


def test_a_conclusive_pair_that_disagrees_attributes_to_neither_side() -> None:
    # Inside then outside 60s later. The participant was in both places and the
    # interval covers a departure, so it belongs to neither.
    trail = [
        *pings([0], distance_m=10.0, accuracy_m=20.0),
        *pings([60], distance_m=2_000.0, accuracy_m=20.0),
    ]

    verification = verify(ping_trail=trail, terms=TERMS, visit_duration_s=600.0, config=CONFIG)

    assert verification.conclusive_pings == 2
    assert verification.inside_s == 0.0
    assert verification.outside_s == 0.0
    assert verification.unattributed_s == 600.0


@pytest.mark.parametrize(
    ("gap_s", "expected_inside_s"),
    [
        (60.0, 60.0),
        (61.0, 0.0),
    ],
)
def test_the_gap_attribution_limit(gap_s: float, expected_inside_s: float) -> None:
    # GAP_ATTRIBUTION_LIMIT_S is the longest gap over which continuity is
    # assumed. The check is `<=`: exactly 60s still counts.
    verification = verify(
        ping_trail=pings([0, gap_s], distance_m=10.0, accuracy_m=20.0),
        terms=TERMS,
        visit_duration_s=600.0,
        config=CONFIG,
    )

    assert verification.inside_s == expected_inside_s


def test_a_trail_handed_over_out_of_order_is_scored_in_received_at_order() -> None:
    # received_at is the sole basis for ordering (spec.md §4), and verification is
    # replayed against stored rows (ADR-0002) whose query order is not verify's to
    # assume. Unsorted, the pairwise walk sees a negative gap and attributes
    # negative time, which no gate is written to survive.
    ordered = pings([0, 60, 120, 180], distance_m=10.0, accuracy_m=20.0)
    shuffled = [ordered[2], ordered[0], ordered[3], ordered[1]]

    verification = verify(ping_trail=shuffled, terms=TERMS, visit_duration_s=600.0, config=CONFIG)

    assert verification.inside_s == 180.0
    assert verification.verdict is Verdict.VERIFIED


# ------------------------------------------------------------------ replay
#
# The reason verification takes distances and a radius rather than pre-classified
# pings: classification depends on radius_m, so freezing it at ingest would make
# the question below unanswerable (ADR-0002).


def test_the_same_trail_rescores_differently_under_a_wider_radius() -> None:
    # Pings 120m out with accuracy 20 straddle a 100m radius and settle wholly
    # inside a 150m one. Same evidence, different terms, different verdict —
    # answerable live in a debrief, against stored rows.
    trail = pings(range(0, 301, 60), distance_m=120.0, accuracy_m=20.0)

    as_assigned = verify(ping_trail=trail, terms=TERMS, visit_duration_s=600.0, config=CONFIG)
    rescored = verify(
        ping_trail=trail,
        terms=AssignmentTerms(radius_m=150.0, min_duration_s=300),
        visit_duration_s=600.0,
        config=CONFIG,
    )

    assert as_assigned.verdict is Verdict.UNVERIFIABLE
    assert as_assigned.conclusive_pings == 0
    assert rescored.verdict is Verdict.VERIFIED
    assert rescored.inside_s == 300.0


def test_verification_is_deterministic_and_stamps_the_rules_that_produced_it() -> None:
    # No clock and no I/O, so re-running is free and gives the same answer. The
    # config version rides along so a stored verdict traces to its rules.
    first = verify(ping_trail=CLEAN_TRAIL, terms=TERMS, visit_duration_s=600.0, config=CONFIG)
    again = verify(ping_trail=CLEAN_TRAIL, terms=TERMS, visit_duration_s=600.0, config=CONFIG)

    assert first == again

    verification = first
    assert verification.scoring_config_version == "v1"
    assert verification.radius_m == 100.0
    assert verification.min_duration_s == 300


# ------------------------------------------------------ terms/config coherence
#
# sufficiency_s is global and min_duration_s is per-assignment, so nothing in the
# type system stops a business setting a minimum shorter than the sufficiency
# threshold. Attributed time cannot exceed the visit it is measured over, so
# VERIFIED becomes unreachable for every visit under such terms.

KIOSK_TERMS = AssignmentTerms(radius_m=100.0, min_duration_s=120)


def test_a_perfect_visit_is_unverifiable_when_min_duration_is_below_sufficiency() -> None:
    # A flawless 150s visit under a 120s minimum: every ping conclusively inside,
    # no gaps, dwell_ratio 1.0. Still UNVERIFIABLE, because 150s of accountable
    # time cannot clear a 180s bar. This is the whole failure mode, and it is
    # silent — the business sees an honest participant they can never verify.
    verification = verify(
        ping_trail=pings([0, 60, 120, 150], distance_m=10.0, accuracy_m=20.0),
        terms=KIOSK_TERMS,
        visit_duration_s=150.0,
        config=CONFIG,
    )

    assert verification.dwell_ratio == 1.0
    assert verification.inside_s == 150.0
    assert verification.verdict is Verdict.UNVERIFIABLE


def test_terms_shorter_than_the_sufficiency_threshold_are_rejected_at_creation() -> None:
    with pytest.raises(IncoherentTermsError, match="min_duration_s"):
        check_terms(KIOSK_TERMS, CONFIG)


def test_terms_exactly_at_the_sufficiency_threshold_are_accepted() -> None:
    # Reachable, though only by a gapless trail. Coherent is the invariant here,
    # not comfortable.
    check_terms(AssignmentTerms(radius_m=100.0, min_duration_s=180), CONFIG)


def test_verify_scores_incoherent_terms_rather_than_raising() -> None:
    # check_terms guards assignment creation, never scoring. Raising here would
    # turn a later SUFFICIENCY_S bump into an outage across every stored visit
    # whose assignment predates it — and replaying history under a newer config
    # is the property ADR-0002 exists to protect.
    stricter = ScoringConfig(sufficiency_s=400, version="v2")

    verification = verify(
        ping_trail=CLEAN_TRAIL, terms=TERMS, visit_duration_s=600.0, config=stricter
    )

    assert verification.verdict is Verdict.UNVERIFIABLE
    assert verification.scoring_config_version == "v2"
