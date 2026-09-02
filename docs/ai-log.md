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

## 2026-09-02 — spec generation reversed a settled decision

Caught by cross-reading `spec.md` against Tuesday's grilling, before any
implementation existed.

- **What happened.** The grilling settled the judgement order as duration first:
  minimum-duration failure is `suspicious`, because "a 90-second visit is
  evidence, not an absence of it". `spec.md` §3 shipped the opposite order —
  sufficiency → duration → dwell — and supplied its own rationale for it
  ("with too little accountable time there is nothing to judge"), so it read as
  reasoned rather than as a slip. Nothing flagged the change.
- **Why it mattered.** The reversal was self-defeating in a way the prose hid.
  Under sufficiency-first, a 90-second all-inside sprint has ~90s attributed,
  fails the sufficiency gate, and returns `unverifiable` — it never reaches the
  duration check. The grilling's position had become unreachable for exactly the
  visits it was decided about. The duration branch survived only for the narrow
  180–300s band.
- **The tell.** The spec kept the grilling's own justifying sentence — "a
  sprint-through *is* evidence, so it is `suspicious`, not `unverifiable`" —
  directly beneath a code block that made it unreachable. Retained rationale
  sitting under reversed logic is the signature of a mechanical reordering, not
  a considered one. Worth generalising: prose and pseudocode in the same section
  are not cross-checked by the thing that writes them.
- **How close it came to being load-bearing.** Issue 02's first test case, "all
  pings inside spanning 90s → `unverifiable`", had already encoded the reversal
  as an assertion. The next step in the plan was to build issue 02 first, on
  Wednesday. One more step and the reversed decision would have been frozen as a
  passing test with a rationale attached, which is the hardest kind to reopen.
- **Decided.** Duration first. The argument that settles it is orthogonality:
  `visit_duration_s` is server-clock evidence, and every mechanism that justifies
  `unverifiable` — pocketed phone, denied permission, indoor accuracy — destroys
  pings without shortening the visit. So ADR-0003's protections survive intact,
  and the reorder touches only visits that are both too short for the task and
  too thin on pings. The decisive point is game-theoretic: under sufficiency-first
  a lazy participant scores *better* by sprinting through (`unverifiable`) than by
  staying away (`suspicious`), which makes brevity the cheapest laundering
  strategy against the one rule the product exists to enforce.
- **Counter-case, accepted not dismissed.** Permission denied on a 60-second
  visit is now `suspicious` where it used to be `unverifiable`. Recorded in
  `spec.md` §3 rather than argued away: no innocent account exists of a 300-second
  task inside a 60-second visit, and false starts do not reach verification at all
  (an abandoned visit never gets a verdict), so a short visit is only judged when
  the participant explicitly ended it *and* reported against it.
- **Applied to:** `spec.md` §3, `CONTEXT.md` (`Unverifiable`/`Suspicious` — the
  old wording defined `suspicious` purely in terms of presence, which the reorder
  outgrows), ADR-0003 (the "mechanism that produces `unverifiable`" claim now
  states *why* it survives duration-first), `docs/design.md` (the "silence feeds
  UNVERIFIABLE, never SUSPICIOUS" measurement note), issue 02's table.
  Issues 04 and 08 turned out not to encode the order at all, and the §9 seed
  scenarios demo `EXPIRED`/`UNREPORTED` rather than verdicts — no change needed
  in either.
- **Second finding from the same trace, unrelated to the order.** The field was
  named `session_duration_s`, and `session` is a `CONTEXT.md` banned word for
  `visit`. It had reached `spec.md` §2, §3 and issue 09 unnoticed. Renamed to
  `visit_duration_s` while it is still only markdown. Same class of drift the ADR
  revision pass caught with "evidence sufficiency" — the vocabulary list does not
  enforce itself, and generated artifacts are where it leaks.
- **Third finding.** §3 gave the signature as `(trail, target, config)`, with
  `session_duration_s` appearing in the judge block from nowhere. Harmless under
  sufficiency-first; fatal under duration-first, where an implementer deriving it
  from first-and-last ping would score every permission-denied visit as
  zero-duration and therefore `suspicious`. Signature now names it explicitly and
  issue 02 calls out the trap.
