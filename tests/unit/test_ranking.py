from app.matching.ranking import rank_candidates
from app.models.driver import Driver

RIDER = (37.7749, -122.4194)  # San Francisco


def make_driver(lat: float, lng: float) -> Driver:
    # Plain in-memory instance, never added to a session — ranking is pure and
    # doesn't need persisted rows (see module docstring for why this test needs
    # no database).
    return Driver(current_lat=lat, current_lng=lng, h3_index="unused")


def test_rank_candidates_orders_by_ascending_distance():
    near = make_driver(37.7750, -122.4195)  # a few meters away
    mid = make_driver(37.8044, -122.2712)  # Oakland, ~13km away
    far = make_driver(34.0522, -118.2437)  # Los Angeles, ~559km away

    ranked = rank_candidates(*RIDER, [far, near, mid])

    assert [driver for driver, _ in ranked] == [near, mid, far]


def test_rank_candidates_returns_distances_ascending():
    a = make_driver(37.7750, -122.4195)
    b = make_driver(37.8044, -122.2712)

    ranked = rank_candidates(*RIDER, [b, a])
    distances = [distance for _, distance in ranked]

    assert distances == sorted(distances)


def test_rank_candidates_empty_list():
    assert rank_candidates(*RIDER, []) == []


def test_rank_candidates_driver_at_rider_location_is_zero_distance():
    here = make_driver(*RIDER)
    ranked = rank_candidates(*RIDER, [here])
    assert ranked == [(here, 0.0)]
