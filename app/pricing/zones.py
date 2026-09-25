"""Surge-pricing zone helper.

A "zone" is an H3 cell at settings.surge_zone_resolution -- deliberately
coarser than (and computed independently from) settings.h3_resolution, the
resolution matching uses for driver candidate discovery. See
app/pricing/surge.py's module docstring for why the two are kept separate.
"""

from app.matching.geo import latlng_to_h3


def zone_id_for_point(lat: float, lng: float, resolution: int) -> str:
    """Maps a coordinate to its surge-pricing zone (an H3 cell address).
    Thin reuse of the same H3 wrapper matching uses -- only the resolution
    differs."""
    return latlng_to_h3(lat, lng, resolution)
