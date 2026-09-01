# Split disclosure: the business never sees the ping trail

The ping trail is retained server-side for scoring and audit, but the business
dashboard shows only the verdict and its breakdown — attributed time inside and
outside, dwell ratio, conclusive ping count — never a movement polyline.
Participants see a consent screen naming what is collected and when it stops,
plus a persistent in-session indicator.

The business's legitimate interest is "was this person at my store". A polyline
answers a much broader question, including where the participant went before and
after, and collecting a justified signal does not license disclosing everything
adjacent to it.

## Considered options

- **Show the business the trail on a map.** The most compelling dashboard
  visual, and their money paid for the visit. Rejected: their interest ends at
  presence; the trail discloses movement beyond the visit's scope, and the
  brief's own privacy prompt exists to catch exactly this conflation.
- **Collect less — score client-side, never upload the trail.** Strongest
  privacy posture. Rejected: verification computed by the untrusted client is
  not verification, and it forfeits replay and audit (ADR-0002).

## Consequences

The map view exists as an internal audit surface, and is demonstrated as
deliberately unexposed rather than quietly omitted. The retention rule — trail
reduced to its verdict breakdown after N days — is simultaneously the privacy
answer and the storage bound, since ping volume grows linearly with active
sessions. One decision covers both. Purge is specified but not implemented in
this slice.
