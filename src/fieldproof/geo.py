"""Geodesy. Distance on the earth's surface, and nothing about judgement.

Separate from `verification` because ingest is the caller: distance is computed
once at write time and stored on the ping row (ADR-0002). Verification consumes
stored distances and never calls this module, so keeping it here means ingest
does not import the pure judgement core to do arithmetic on coordinates.
"""

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
