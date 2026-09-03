"""Constants that are not judgement thresholds (spec.md §1).

The judgement thresholds live in `verification.ScoringConfig`, where they are
versioned and stamped onto every verdict (ADR-0002). What is here is the other
two tables in spec.md §1: the operational timings, which no verdict depends on,
and the defaults for the columns that legitimately vary per assignment.

Three of those, not §1's original two: `report_deadline_s` moved here from the
operational timings, because the write-up window is a fact about the task rather
than about the server. §1 and §2 are updated to match.

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

DEFAULT_REPORT_DEADLINE_S = 86_400
"""24 hours to write up a sealed visit. `UNREPORTED` is unrecoverable — the
participant has already left the site and cannot re-run a visit to attach prose —
so the default has to be generous.

Per assignment rather than global, and therefore listed here rather than under
the operational timings below: how long a write-up may take is a fact about the
*task* — a same-day mystery-shop report and a monthly compliance audit differ the
way `radius_m` and `min_duration_s` differ — not a property of the server. It is
read once, when a visit is sealed, to set that visit's `report_deadline_at`."""

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

BACKFILL_GRACE_S = 60
"""Pings whose client clock is older than this are rejected (spec.md §4).
Deliberately the same 60s as `ScoringConfig.gap_attribution_limit_s`: one
concept, two uses."""

SSE_KEEPALIVE_S = 15
"""How long a quiet dashboard stream goes between comment frames.

A subscriber that vanishes without closing its socket — a phone walking out of
coverage — is invisible to the server until the server tries to write to it.
The keepalive is that write: it turns "a queue held forever" (issue 06's named
risk) into "a queue held for at most this long", and it keeps intermediaries
from timing the connection out as idle. Same cadence as `PING_INTERVAL_S`, for
no deeper reason than that one rhythm is easier to reason about than two."""
