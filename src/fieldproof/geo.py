"""Geodesy. Distance on the earth's surface, and nothing about judgement.

Separate from `verification` because ingest is the caller: distance is computed
once at write time and stored on the ping row (ADR-0002). Verification consumes
stored distances and never calls this module, so keeping it here means ingest
does not import the pure judgement core to do arithmetic on coordinates.
"""

from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt

EARTH_RADIUS_M = 6_371_008.8
"""Mean earth radius (IUGG). Spherical: good to a few tenths of a percent."""


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in metres between two coordinates."""
    phi1, phi2 = radians(lat1), radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = radians(lng2 - lng1)
    h = sin(d_phi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(d_lambda / 2) ** 2
    # h is mathematically bounded by 1 but is a sum of rounded terms, and `sin`
    # and `cos` are platform libm calls with no correct-rounding guarantee. A
    # near-antipodal h a few ulp over 1 would put sqrt(h) outside asin's domain
    # and raise. Unreachable at the scale this product works at, and cheaper to
    # exclude than to reason about per-platform.
    return 2 * EARTH_RADIUS_M * asin(min(1.0, sqrt(h)))


@dataclass(frozen=True)
class TargetLocation:
    """The coordinates a business wants attended, with the radius (CONTEXT.md).

    Issue 01's review flagged the `(target_lat, target_lng, radius_m)` clump and
    the finding was declined for want of a second caller — this module's own
    precedent being to move on a real caller rather than an anticipated one.
    Ingest is that caller: it reads all three columns for every ping, to compute
    `distance_m` and then to classify it against the radius.

    It lives here rather than in `verification` because `verify` must not acquire
    a coordinate. Judgement consumes stored distances (ADR-0002) and would then
    be carrying a target around only to ignore it. `AssignmentTerms` keeps its
    own `radius_m` rather than composing this type for the same reason:
    `radius_m` is the one field of the three that judgement genuinely needs, and
    nesting would force every re-scoring caller to supply two floats `verify`
    never reads.

    `distance_m` is a method rather than a bare `haversine_m` call at each site
    because `haversine_m(lat1, lng1, lat2, lng2)` takes four interchangeable
    floats: transposing the target and the reported position is silent (the
    result is symmetric) and wrong in nothing but which pair came from the
    client. Naming the target makes that transposition unrepresentable.
    """

    lat: float
    lng: float
    radius_m: float

    def distance_m(self, *, lat: float, lng: float) -> float:
        """Great-circle metres from this target to a reported position."""
        return haversine_m(self.lat, self.lng, lat, lng)
