# Business dashboard (Angular)

Status: ready-for-agent
Blocked by: 06, 08

Consumes the SSE stream: render the snapshot, apply deltas.

Shows per visit: assignment, state, verdict, and the breakdown — attributed time,
dwell ratio, conclusive ping count, session duration. Plus attempt count per
assignment, which is signal in its own right (ADR-0001).

**No polyline, ever** (ADR-0005). The business sees the verdict and its
reasoning, not where the participant walked.

Completion and verdict are separate columns. A `suspicious` verdict is a prompt
for a human to look, not a rejection (ADR-0004).

Cut: the ticking "last seen Ns ago" counter and the map. The map may exist as an
internal audit route if time allows — demonstrate it as deliberately unexposed
rather than omitting it silently.
