# Accuracy is an uncertainty interval, never a trust weight

The intuitive design weights each ping by its reported GPS accuracy, so precise
pings count for more. Accuracy is a number the client puts in the request body,
so that design pays the most attention to whichever pings are easiest to
fabricate: a spoofer sends `accuracy: 3` and outscores an honest participant
standing indoors. We inverted it. A ping at distance `d` with accuracy `a` against
radius `R` is conclusively inside when `d + a < R`, conclusively outside when
`d - a > R`, and otherwise **inconclusive** — counting toward neither side.

## Considered options

- **Weight by accuracy** (`weight = 1/a` or similar). Rejected above: it rewards
  the only party with an incentive to lie about the field.
- **Reject pings above an accuracy cutoff.** Rejected as unnecessary: under the
  interval rule a ping with `accuracy: 800` is already inconclusive against a
  100m radius. The arithmetic absorbs junk data without a special case.

## Consequences

Self-reported precision earns nothing, so there is no incentive to inflate it.
Poor accuracy makes a ping count for nothing rather than counting against the
participant, which is the correct treatment for the indoor case — and indoors is
where mystery shopping happens, so it is the normal case, not the edge case. This
is also the mechanism that produces `unverifiable` rather than `suspicious` when
a participant's hardware or building is the problem.

That claim survives the judgement order in `spec.md` §3, where the
minimum-duration test runs ahead of the sufficiency test. Bad accuracy, a denied
permission and a pocketed phone all destroy pings without shortening the visit,
so a participant whose hardware failed still clears the duration gate and still
lands on `unverifiable`. Duration-first reclassifies only visits that were *also*
too short for the task — which no hardware or building problem produces.
