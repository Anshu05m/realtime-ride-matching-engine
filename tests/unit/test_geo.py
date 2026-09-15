import h3

from app.matching.geo import cells_within_ring, haversine_km, latlng_to_h3

SF = (37.7749, -122.4194)
LA = (34.0522, -118.2437)


def test_latlng_to_h3_returns_valid_cell():
    cell = latlng_to_h3(*SF, 8)
    assert h3.is_valid_cell(cell)


def test_latlng_to_h3_is_deterministic():
    assert latlng_to_h3(*SF, 8) == latlng_to_h3(*SF, 8)


def test_latlng_to_h3_different_points_can_differ():
    assert latlng_to_h3(*SF, 8) != latlng_to_h3(*LA, 8)


def test_cells_within_ring_zero_is_self():
    cell = latlng_to_h3(*SF, 8)
    assert cells_within_ring(cell, 0) == {cell}


def test_cells_within_ring_one_has_six_neighbors():
    cell = latlng_to_h3(*SF, 8)
    ring1 = cells_within_ring(cell, 1)
    assert len(ring1) == 6
    assert cell not in ring1


def test_cells_within_ring_no_overlap_across_rings():
    cell = latlng_to_h3(*SF, 8)
    ring0 = cells_within_ring(cell, 0)
    ring1 = cells_within_ring(cell, 1)
    ring2 = cells_within_ring(cell, 2)
    assert ring0.isdisjoint(ring1)
    assert ring0.isdisjoint(ring2)
    assert ring1.isdisjoint(ring2)


def test_cells_within_ring_rejects_negative_k():
    cell = latlng_to_h3(*SF, 8)
    try:
        cells_within_ring(cell, -1)
        raised = False
    except ValueError:
        raised = True
    assert raised


def test_haversine_km_zero_for_identical_points():
    assert haversine_km(*SF, *SF) == 0.0


def test_haversine_km_known_reference_distance():
    # SF to LA is ~559km great-circle distance.
    distance = haversine_km(*SF, *LA)
    assert 550 <= distance <= 570


def test_haversine_km_is_symmetric():
    assert haversine_km(*SF, *LA) == haversine_km(*LA, *SF)
