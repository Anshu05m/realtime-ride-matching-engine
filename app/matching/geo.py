"""H3 cell helpers and geographic distance calculation.

H3 is used for candidate *discovery* only (cheap: "which drivers are in this set
of nearby cells" is an indexed equality/IN query). It is deliberately not used to
rank candidates — hex-cell adjacency is not a reliable proxy for real distance,
so ranking (see ranking.py) always falls back to actual haversine distance.
"""

import math

import h3


def latlng_to_h3(lat: float, lng: float, resolution: int) -> str:
    """Maps a coordinate to its H3 cell address at the given resolution."""
    return h3.latlng_to_cell(lat, lng, resolution)


def cells_within_ring(h3_index: str, k: int) -> set[str]:
    """Returns the cells exactly k rings away from h3_index (k=0 -> {h3_index}).

    Implemented as disk(k) - disk(k-1) rather than h3.grid_ring, because grid_ring
    can return distorted/incomplete results near H3's 12 pentagon cells (an
    unavoidable artifact of tiling a sphere with hexagons). grid_disk is always a
    well-defined filled set, so subtracting two disks is a safer way to get "ring
    k" than asking for it directly.
    """
    if k < 0:
        raise ValueError("k must be >= 0")
    outer = set(h3.grid_disk(h3_index, k))
    if k == 0:
        return outer
    inner = set(h3.grid_disk(h3_index, k - 1))
    return outer - inner


_EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance between two lat/lng points, in kilometers."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lng2 - lng1)

    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return _EARTH_RADIUS_KM * c
