# AI process note

One page over `docs/ai-log.md`, which is the running record. Everything below
is dated there; this is the shape of it and the headline moments.

## Tooling

Claude Code, with the `mattpocock-skills` plugin (v1.2.3, user-scope install,
commit `0ab1b63`) supplying the process skills, and ruff, mypy strict and pytest
as the four gates every change had to pass. The repository-level configuration
the agent read on every session is inventoried at the end; the skill files it
ran are copied verbatim into `docs/agent-config/`.

## The pipeline

**grill → spec → tickets → implement**, with the design phase run to an empty
question frontier before any implementation code existed.

1. **Grilling with docs** (2026-09-01). `/grill-with-docs` runs `grilling`
   (a relentless interview, worked as a design tree in rounds, each question
   carrying the skill's recommended answer) together with `domain-modeling`
   (challenging terms, writing the glossary and ADRs the moment a decision
   crystallises). Twenty-two questions. Output: `CONTEXT.md`, six ADRs each
   with its rejected alternatives, and the iOS suspension measurement, because
   one question could only be answered by measuring.
2. **Spec** (2026-09-01). `/to-spec` synthesised the grilling into
   `spec.md`: constants, schema, algorithm, transition tables, endpoints, cut
   line. `/to-tickets` cut it into ten tracer-bullet issues with blocking
   edges, published as local markdown under `.scratch/` (the tracker
   `/setup-matt-pocock-skills` configured). `/handoff` wrote the plan for the
   build days.
3. **Revision pass** (2026-09-02). Human edits to three ADRs, reviewed by the
   skill; then a human cross-read of `spec.md` against the grilling, which
   found the spec had reversed a settled decision (below).
4. **Implement** (2026-09-02 to 2026-09-04), one issue at a time in dependency
   order: 02, 03, 01, 04, 05, 06, 08, 07, 09. On every issue the seams under
   test were put up with recommendations and agreed before any test was
   written. The pure modules, verification (02) and the state machines (03),
   were built with `/tdd`, red then green, with the count of genuinely red
   cycles recorded rather than claimed. Everything with a database or a socket
   behind it was built seams-first and then **mutation-tested** whenever a test
   was green on arrival: a defect smuggled into the shipped code, the suite run,
   the failure confirmed, the defect reverted, the miss recorded. After each
   issue, `/code-review` ran its two axes in parallel, Standards (the repo's
   documented conventions plus a smell baseline) and Spec (the originating
   issue), and the findings and their dispositions were appended to the issue
   file. Then the phone: a smoke test on 2026-09-03 and a full field test on
   2026-09-04.

## Headline overrides: human over AI

- **2026-09-01, schedule risk.** The skill named Angular as the risk and
  proposed compressing it. Rejected: Angular was the fastest layer; the risk
  was the async FastAPI work, so the buffer moved to Wednesday.
- **2026-09-01, a platform fact.** The skill recalled mobile browsers
  *throttling* background timers to about once a minute. Measured instead:
  iOS Safari suspends JavaScript entirely on lock or app switch, a single
  unbounded gap (297.8 s for a 5-minute lock). This set the 15-minute
  abandonment timeout. The AI was right that the fact was load-bearing and
  right to push for measurement; it was wrong about the fact.
- **2026-09-02, a gap the grilling missed.** Twenty-two questions never asked
  how an assignment gets its participant. The brief's phrasing smuggled the
  pre-assigned model in and the AI inherited it unexamined. Decided by the
  human: pre-assigned for the slice, marketplace named in the pushback section.
- **2026-09-02, a more privacy-protective alternative kept.** The skill's ADR
  review had not considered "score client-side, never upload the trail" as a
  rejected option in ADR-0005. Kept, because it is rejected for a reason worth
  recording (the untrusted client cannot verify itself).
- **2026-09-03, Postgres over SQLite.** The agent recommended SQLite on the
  strength of the fresh-clone test. Overridden; the agent then found the
  stronger argument for the override (native `timestamptz` holds the
  timezone-aware rule at the driver boundary) and flagged the cost, updating
  `CLAUDE.md` rather than leaving it for Friday.
- **2026-09-03, the report window is a task fact.** The seeded
  `PENDING_REPORT` visit was reversed the day it landed: the window belongs on
  the assignment beside `radius_m` and `min_duration_s`, and the seed plants no
  visits. The agent moved the constant, updated the spec in three places, and
  corrected the test count rather than re-banking it.
- **2026-09-03, decision A.** An assignment could expire beneath a live visit.
  Found by the sweeper's tests, implemented as written, routed to the human.
  Decided: the deadline means *start by*; the expiry sweep skips assignments
  with a non-terminal visit.

## Headline catches

Caught by the AI:

- **2026-09-01.** A contradiction between two of its own answers: the trail on
  a map as the flagship demo (Q4) versus the business over-disclosure that
  ADR-0005 now forbids (Q9). A self-correction on what the brief actually
  requires of the live dashboard (Q6). ADR-0006 resting on a feature that had
  been cut; rewritten to rest on the sweeper's server-originated transitions.
- **2026-09-02.** A soft contradiction between the human's edits to ADR-0002
  and ADR-0005, resolved as "the replay window and the privacy window are the
  same window". Vocabulary drift in a human edit ("evidence sufficiency"). Its
  own circular distance test, re-derived from Vincenty. Where the incoherent
  terms guard belongs (assignment creation, never inside `verify`, or a config
  bump becomes an outage across history). Declining to write a crash test it
  could not make fail.
- **2026-09-03.** Its own overclaim that mypy would catch a missing `match`
  arm. A false citation of `spec.md` §10 in a docstring, found on review. 422
  rather than 409 for a stale ping, argued from the client's own rule in the
  spec. The real row-lock race (the `await` between the state check and the
  ping insert), and two validations nobody asked for: negative accuracy as a
  spoofing vector, naive timestamps refused. Two real design defects from a
  mutation campaign: an unobservable self-loop guard and an unbounded
  shutdown.
- **2026-09-04.** A surviving mutation (the report handler's lock scoped to the
  visit alone) closed by staging the race through the endpoint, where it is a
  lost update.

Caught by the human:

- **2026-09-02, the spec reversed a settled decision.** The grilling settled
  duration-first judgement; `spec.md` shipped sufficiency-first with its own
  plausible rationale, and issue 02's first test case had already encoded the
  reversal. Found by cross-reading the spec against the transcript, one step
  before it would have been frozen as a passing test. The tell, worth
  generalising: the spec kept the grilling's justifying sentence directly under
  pseudocode that made it unreachable. Two more findings came out of the same
  trace: a banned word (`session`) that had reached three documents, and a
  signature that let an implementer derive visit duration from the trail.
- **2026-09-03, context as a variable.** At about 180k tokens a session entered
  a polish-and-verify loop on already-proven code. Stopped with a closed
  checklist. Directing the agent includes noticing when its context, not its
  judgement, is driving.

Honest accounting throughout: 6 of 13 cycles genuinely red on issue 02, 7 of
10 on issue 03, 0 of 11 on issue 01, each stated in the issue rather than
dressed up.

## Configuration inventory

What the agent read at the start of every session, as used:

| File | Role |
| --- | --- |
| `CLAUDE.md` | Project instructions: the setup, the four gates, the conventions (mypy strict, coded ignores, timezone-aware datetimes, no blocking calls in `async def`, `unused-awaitable`, pure verification, glossary vocabulary in identifiers), and the `## Agent skills` block pointing at `docs/agents/`. |
| `docs/agents/issue-tracker.md` | Issues as local markdown under `.scratch/<feature>/`, one file per ticket, comments appended. Written by `/setup-matt-pocock-skills` on 2026-09-01. |
| `docs/agents/triage-labels.md` | The five triage roles as `Status:` values. Same origin. |
| `docs/agents/domain.md` | Read `CONTEXT.md` and the ADRs before exploring; use the glossary's words; flag ADR conflicts rather than override them. Same origin. |
| `CONTEXT.md`, `docs/adr/` | The domain docs those rules point at. |
| `.claude/settings.local.json` | Two pre-approved commands: `pytest -q` and `pytest tests/test_transitions.py -q`. |
| `~/.claude/settings.json` (user scope, not in the repo) | Enables `mattpocock-skills@claude-plugins-official`; model `claude-fable-5-1[1m]`. |
| `docs/agent-config/` | Verbatim copies of the plugin's skill files that were run, plus its `plugin.json` and MIT `LICENSE`. See its `README.md` for which was used where. |

Not in the repository at this commit: the grilling transcript itself, which
the log refers to as kept as evidence. It lives in the Claude Code session
history for 2026-09-01.
