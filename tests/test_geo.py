"""Geodesy (src/fieldproof/geo.py)."""

import pytest

from fieldproof.geo import haversine_m

# ---------------------------------------------------------------- haversine_m

#
# Expected values come from Vincenty's ellipsoidal formula on WGS84 — a different
# derivation from the haversine under test, so agreement is evidence rather than
# an identity. A spherical earth tracks the ellipsoid to within ~0.7% at these
# latitudes, which is the tolerance below and is what this codebase is buying:
# at the few-hundred-metre scale a radius check works on, 0.7% is sub-metre,
# far inside the accuracy_m every ping carries anyway.

ELLIPSOID_TOLERANCE = 0.007


def test_distance_between_a_point_and_itself_is_zero() -> None:
    assert haversine_m(51.5007, -0.1246, 51.5007, -0.1246) == 0.0


def test_distance_at_the_scale_a_radius_check_works_at() -> None:
    # 150.20 m by Vincenty. This is the range the whole product operates in.
    assert haversine_m(51.5007, -0.1246, 51.50205, -0.1246) == pytest.approx(
        150.20, rel=ELLIPSOID_TOLERANCE
    )


def test_one_degree_of_latitude_at_the_equator() -> None:
    # 110,574.4 m by Vincenty. Note this is *not* the 111,195 m a spherical model
    # gives: asserting that figure would just be restating the implementation.
    assert haversine_m(0.0, 0.0, 1.0, 0.0) == pytest.approx(110_574.4, rel=ELLIPSOID_TOLERANCE)


def test_longitude_degrees_narrow_towards_the_poles() -> None:
    # 78,846.3 m by Vincenty at 45°N, against 111 km at the equator. A flat
    # lat/lng subtraction would miss this entirely.
    assert haversine_m(45.0, 0.0, 45.0, 1.0) == pytest.approx(78_846.3, rel=ELLIPSOID_TOLERANCE)


def test_london_to_paris() -> None:
    # 340,894.8 m by Vincenty, Big Ben to the Eiffel Tower.
    assert haversine_m(51.5007, -0.1246, 48.8584, 2.2945) == pytest.approx(
        340_894.8, rel=ELLIPSOID_TOLERANCE
    )


WGS84_HALF_MERIDIAN_M = 20_003_931.5
"""Pole to pole on the ellipsoid. The spherical model overstates it by ~0.06%."""


@pytest.mark.parametrize(
    ("lat1", "lng1", "lat2", "lng2"),
    [
        (90.0, 0.0, -90.0, 0.0),
        # This pair drives the intermediate `h` one ulp above 1.0 on this
        # platform, which is the input the asin domain clamp exists for.
        (-87.5, 0.0, 87.5, 180.0),
    ],
)
def test_antipodal_points_are_half_a_circumference_apart_and_do_not_raise(
    lat1: float, lng1: float, lat2: float, lng2: float
) -> None:
    assert haversine_m(lat1, lng1, lat2, lng2) == pytest.approx(
        WGS84_HALF_MERIDIAN_M, rel=ELLIPSOID_TOLERANCE
    )
