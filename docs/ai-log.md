# AI Process Log

## 2026-09-01 — Design grilling (grill-with-docs, Claude Code)

- Ran a grilling session on the full design before writing any code;
  transcript kept as evidence.
- AI self-correction (Q6): skill first framed live in-flight dashboard
  rows as implied by the brief, then corrected itself — brief step 6
  only requires completed visits to appear automatically. I adopted
  the in-flight view as my own deliberate scope addition, cuttable
  if time runs short.
- AI contradiction caught (Q4 vs Q9): Q4 recommended showing the
  raw ping trail on a map as the flagship demo. Q9 flagged that a
  business-facing trail over-discloses — conflicts with the brief's
  privacy requirement. Resolution: trail stays server-side, business
  sees verdict + breakdown only, map becomes an internal audit view.

- Override (Q18, schedule): the skill named Angular as my schedule risk and
  proposed compressing it. Rejected — Angular is my primary stack and my fastest
  layer. The real risk is the async FastAPI work (sweeper, SSE fan-out), so the
  buffer moved to Wednesday and the frontend was compressed instead.
- AI got a platform fact wrong (Q13): the skill recalled mobile browsers as
  _throttling_ background timers to roughly once a minute. I measured it instead
  of taking it (`docs/experiments/pingtest.html`): iOS Safari suspends JS
  **entirely** on screen lock and on app switch — a single unbounded gap, 297.8s
  for a ~5min lock. Categorically worse than throttling, and it is what set the
  15-minute abandonment timeout. The AI was right that the fact was load-bearing
  and right to push me to measure rather than assume; it was wrong about the fact.
- AI inconsistency caught late (ADR-0006): the skill justified SSE over polling
  partly on the live in-flight dashboard, which I then cut for time in Q18 —
  leaving the ADR resting on a feature that no longer exists. Flagged and
  rewritten to rest on the sweeper instead: `ABANDONED`, `UNREPORTED` and
  `EXPIRED` are server-originated with no request behind them, which motivates a
  push channel independently. Polling is recorded as a reasonable alternative
  rather than a strawman.

## 2026-09-01 — end of design session

Design closed with an empty question frontier. Six ADRs, a spec, ten ordered
issues and a handoff note, all generated from the grilling transcript before any
implementation code was written.

## 2026-09-02 — ADR revision pass

- I revised three ADRs after the design session; the skill reviewed them and
  disagreed on one. My addition to ADR-0002 restated ADR-0005's privacy/storage
  argument, which left the two ADRs softly contradicting each other: 0002 claimed
  historical visits can be re-scored, 0005 said trails are reduced after N days.
  Rewritten to state the trade-off instead — the replay window and the privacy
  window are the same window. The contradiction was mine; the catch was the AI's.
- Skill also flagged vocabulary drift in my own edit: I wrote "evidence
  sufficiency" in ADR-0005's breakdown list, which is not a `CONTEXT.md` term
  (sufficiency is a threshold test, not a displayed quantity). Reverted to
  "conclusive ping count".
- Kept over the skill's original: my "score client-side, never upload the trail"
  alternative in ADR-0005 — a _more_ privacy-protective option rejected on
  security grounds, which the skill had not considered.
- Gap the grilling missed, caught by me on review: 22 questions
  never asked how an assignment gets its participant — one
  pre-assigned person, or an open pool participants claim from.
  The brief's phrasing ("a participant is assigned a task")
  smuggled the answer in, and the AI inherited it unexamined.
  Decided: pre-assigned for the slice, marketplace named as the
  likely real-product model in the design writeup's pushback
  section.
