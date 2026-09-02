# fieldproof

Verifying that a field participant physically attended an assigned location, from
evidence their own browser produced, without a human reviewing every visit.

This file is the glossary and nothing else. Decisions live in `docs/adr/`.

## Language

### Parties

**Business**:
The party that wants a location visited and a task performed there. Sees verdicts,
never raw location data.
_Avoid_: client, merchant, customer

**Participant**:
The person who travels to the target location and performs the task.
_Avoid_: shopper, user, worker, agent

### Work

**Assignment**:
A task issued by a business to a participant, bound to a target location and a
deadline. Not an attempt to perform the task — that is a visit.
States: `ASSIGNED`, `EXPIRED`, `FULFILLED`.
_Avoid_: task, job, booking

**Visit**:
One attempt by a participant to perform an assignment, and the unit that carries
location evidence. An assignment may have many visits; at most one non-terminal.
States: `ACTIVE`, `PENDING_REPORT`, `COMPLETED`, `ABANDONED`, `UNREPORTED`.
_Avoid_: session, attempt, check-in

**Target Location**:
The coordinates a business wants attended, with the radius inside which a
participant counts as present.
_Avoid_: venue, site, geofence

**Report**:
The participant's written account of what they did at the target location,
submitted after the visit is sealed.
_Avoid_: submission, feedback, writeup

### Evidence

**Ping**:
One reported position from a participant's browser during an active visit:
coordinates, the browser's own accuracy estimate, and the server's receipt time.
_Avoid_: location update, heartbeat, fix

**Ping Trail**:
The ordered pings of one visit, sealed when the visit leaves `ACTIVE`.
_Avoid_: track, path, route, breadcrumb

**Accuracy**:
The browser's own estimate of a ping's positional uncertainty, in metres.
Client-supplied, so it describes uncertainty and never trustworthiness.
_Avoid_: precision, confidence

**Conclusive**:
Of a ping: one whose accuracy interval falls wholly inside or wholly outside the
target radius, so it can be judged. Otherwise it is **inconclusive** and counts
for neither side.
_Avoid_: valid, accepted, good

**Attributed Time**:
Session time that can be assigned to inside or outside, being the intervals
between consecutive agreeing conclusive pings that are close enough together to
assume continuity. Everything else is unattributed.
_Avoid_: tracked time, covered time

**Dwell Ratio**:
Attributed time inside the radius over all attributed time.
_Avoid_: presence score, accuracy percentage, hit rate

### Judgement

**Verification**:
The pure computation from a sealed ping trail, a target location and a scoring
config to a verdict. Deterministic and re-runnable.
_Avoid_: validation, checking, fraud detection

**Verdict**:
The outcome of verification for a visit: `verified`, `suspicious`, or
`unverifiable`. Never a boolean.
_Avoid_: status, result, flag, score

**Unverifiable**:
Too little attributed time to judge presence either way, on a visit that ran long
enough for the task. An absence of evidence.
_Avoid_: failed, invalid, rejected

**Suspicious**:
Enough evidence to judge, and it argues against the assignment having been
performed — attributed time landing mostly outside the radius, or a visit too
short for the task whatever the pings say. Evidence of absence.
_Avoid_: fraudulent, fake, rejected

**Scoring Config**:
The versioned judgement thresholds verification runs against, stamped onto every
verdict so a stored result traces to the rules that produced it.
_Avoid_: settings, rules, params

**Fulfilment**:
An assignment's task being done, established by any completed visit. Independent
of that visit's verdict.
_Avoid_: approval, acceptance, payout
