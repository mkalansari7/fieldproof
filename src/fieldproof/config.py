"""Constants that are not judgement thresholds (spec.md §1).

The judgement thresholds live in `verification.ScoringConfig`, where they are
versioned and stamped onto every verdict (ADR-0002). What is here is the other
two tables in spec.md §1: the operational timings, which no verdict depends on,
and the defaults for the two columns that legitimately vary per assignment.

`SCORING_CONFIG` is the one live instance of the thresholds. Named here so that
assignment creation, the report handler and any re-scoring all reach for the same
object rather than each constructing their own.
"""

from fieldproof.verification import ScoringConfig

SCORING_CONFIG = ScoringConfig()
"""The thresholds in force. Bump `ScoringConfig.version` when any of them change."""

# ---------------------------------------------------------------- per-assignment defaults

DEFAULT_RADIUS_M = 100.0
"""Must clear the indoor accuracy floor: conclusive-inside needs `d + a < R`, and
indoor accuracy runs 30-100m, so a 50m radius makes every indoor visit
unverifiable. A kiosk and a shopping mall genuinely differ (spec.md §1)."""

DEFAULT_MIN_DURATION_S = 300
"""A coffee run and a bank branch audit are not the same task. Above
`ScoringConfig.sufficiency_s`, which `verification.check_terms` enforces."""

# ---------------------------------------------------------------- operational timings

PING_INTERVAL_S = 15
"""Client cadence (spec.md §8)."""

SWEEP_TICK_S = 10
"""Sweeper loop (spec.md §7)."""

ABANDON_AFTER_S = 900
"""15 minutes of silence on an `ACTIVE` visit. Derived from measurement, not
chosen: iOS Safari suspends JS entirely on screen lock, and a ~5 minute lock
produced a single 297.8s gap (`docs/design.md`). Anything shorter marks honest
visits abandoned."""

REPORT_DEADLINE_S = 86_400
"""24 hours. `UNREPORTED` is unrecoverable — the participant has already left the
site and cannot re-run a visit to attach prose — so it has to be generous."""

BACKFILL_GRACE_S = 60
"""Pings whose client clock is older than this are rejected (spec.md §4).
Deliberately the same 60s as `ScoringConfig.gap_attribution_limit_s`: one
concept, two uses."""
