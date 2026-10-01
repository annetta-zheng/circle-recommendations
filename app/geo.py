"""Resolve a `location` query parameter to a city the data knows.

The dataset carries city strings only ("Seattle, WA"), never coordinates, so a
`lon,lat` pair is snapped to the nearest known city within CITY_MATCH_RADIUS_KM
and anything farther resolves to no city (the same-city signal is then off, as
it is for members with no location). A city string is matched case-insensitively
against the known spellings; an unknown string passes through unchanged and
simply matches no members.
"""

from __future__ import annotations

import math
import re

from app import config

_COORDS = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")
_BY_LOWER = {name.lower(): name for name in config.CITY_COORDS}


def _haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dlmb = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def nearest_city(lon: float, lat: float) -> str | None:
    best, best_km = None, config.CITY_MATCH_RADIUS_KM
    for name, (clon, clat) in config.CITY_COORDS.items():
        km = _haversine_km(lon, lat, clon, clat)
        if km <= best_km:
            best, best_km = name, km
    return best


def resolve(location: str | None) -> str | None:
    """None -> None; "lon,lat" -> nearest known city or None; "City, ST" -> canonical spelling."""
    if location is None:
        return None
    m = _COORDS.match(location)
    if m:
        lon, lat = float(m.group(1)), float(m.group(2))
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            raise ValueError("location coordinates must be lon,lat with lon in [-180,180] and lat in [-90,90]")
        return nearest_city(lon, lat)
    text = location.strip()
    return _BY_LOWER.get(text.lower(), text) or None
