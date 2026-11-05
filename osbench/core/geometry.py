"""Distances and shipping zones.

v1 — pure numpy. Functions are marked for possible Numba compilation later
(without changing signatures). For determinism: no parallel reductions, fastmath off.
"""

from __future__ import annotations

import numpy as np

_EARTH_R_KM = 6371.0088


def distance_km(x1: np.ndarray, y1: np.ndarray, x2: np.ndarray, y2: np.ndarray, mode: str):
    """Distance (km). plane: Euclidean over km coordinates; haversine: (x=lon, y=lat) in degrees."""
    if mode == "plane":
        return np.sqrt((x1 - x2) ** 2 + (y1 - y2) ** 2)
    if mode == "haversine":
        lon1, lat1, lon2, lat2 = map(np.radians, (x1, y1, x2, y2))
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
        return 2 * _EARTH_R_KM * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))
    raise ValueError(f"unknown geo.mode: {mode}")


def zone_of_km(km: np.ndarray, zone_bounds_km: np.ndarray) -> np.ndarray:
    """Zone index = first threshold that is >= distance."""
    return np.searchsorted(zone_bounds_km, km, side="left").astype(np.int32)
