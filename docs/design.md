## Measured: iOS Safari suspension (iPhone, Safari, 2026-09-01)

Test: page logging a timestamp every 15s, served locally.

- Screen locked ~2.5 min → single gap of 147s (no ticks during lock)
- Screen locked ~5 min → single gap of 297.8s
- Backgrounded to another app ~2 min → single gap of 116.4s
  In all cases suspension was total and immediate; ticks resumed
  instantly on return. Gap length ≈ time away, unbounded.
  Consequence: ping silence cannot distinguish "pocketed phone"
  from "left the site" — timeout set to 15 min and silence feeds
  UNVERIFIABLE, never SUSPICIOUS. Wake Lock + explicit "keep page
  open" guidance is the primary mitigation.
  Caveat: one device, one browser; Android/Chrome not measured.

## Scale (to write, day 4)

- Privacy retention = storage bound (one decision, two jobs)
- Production path not built: queued/batched ingest, time-partitioned ping table

## Pushback (to expand, Friday)

- My measurement is the core argument: iOS suspends JS entirely
  when pocketed/backgrounded — the primary signal disappears when
  the user does the most natural thing with a phone. GPS-only
  verification is therefore a laziness filter, not fraud
  prevention (see Measured section). Wake Lock + "keep page open"
  aren't polish; the product depends on them. v2: signals that are
  cheap for honest presence (QR on site / timed photo), not
  costlier fraud detection.
