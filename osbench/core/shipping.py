"""Tariffs and delivery times by zone."""

from __future__ import annotations

import numpy as np

from ..config.schema import TariffConfig


class ShippingModel:
    """Compute parcel cost and delivery time by zone (vectorized)."""

    def __init__(self, tariffs: TariffConfig):
        self.bounds = np.asarray(tariffs.zone_bounds_km, dtype=np.float64)
        self.cost = np.asarray(tariffs.cost_by_zone, dtype=np.float64) * tariffs.steepness
        self.per_kg = np.asarray(tariffs.per_kg_by_zone, dtype=np.float64) * tariffs.steepness
        self.days = np.asarray(tariffs.transit_days_by_zone, dtype=np.int64)
        self.noise_mean = tariffs.delivery_noise_days_mean

    def zone(self, km: np.ndarray) -> np.ndarray:
        return np.searchsorted(self.bounds, km, side="left").astype(np.int64)

    def parcel_cost(self, zone: np.ndarray, weight_kg: np.ndarray) -> np.ndarray:
        return self.cost[zone] + self.per_kg[zone] * weight_kg

    def transit_days(self, zone: np.ndarray, pack_after_cutoff: np.ndarray | bool = False):
        d = self.days[zone]
        return d + np.asarray(pack_after_cutoff, dtype=np.int64)
