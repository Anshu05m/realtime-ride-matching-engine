import h3

from app.pricing.zones import zone_id_for_point

SF = (37.7749, -122.4194)
LA = (34.0522, -118.2437)


def test_zone_id_for_point_returns_valid_cell():
    zone = zone_id_for_point(*SF, 6)
    assert h3.is_valid_cell(zone)


def test_zone_id_for_point_is_deterministic():
    assert zone_id_for_point(*SF, 6) == zone_id_for_point(*SF, 6)


def test_zone_id_for_point_differs_for_distant_points():
    assert zone_id_for_point(*SF, 6) != zone_id_for_point(*LA, 6)


def test_zone_id_for_point_respects_resolution():
    # A coarser resolution covers strictly more ground, so two nearby points
    # (here, ~0.5km/0.7km apart) that fall in different fine cells can still
    # share the same coarse zone.
    point_a = (37.7749, -122.4194)
    point_b = (37.7799, -122.4244)

    coarse_a = zone_id_for_point(*point_a, 4)
    coarse_b = zone_id_for_point(*point_b, 4)
    fine_a = zone_id_for_point(*point_a, 9)
    fine_b = zone_id_for_point(*point_b, 9)

    assert coarse_a == coarse_b
    assert fine_a != fine_b
