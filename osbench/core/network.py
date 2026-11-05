"""Static network representation for candidate computation (numpy arrays)."""

from __future__ import annotations

import numpy as np

from ..config.schema import ScenarioConfig
from ..generate.world import World
from .shipping import ShippingModel


class Network:
    """Immutable network arrays: warehouse coordinates, SKU weights, tariffs."""

    def __init__(self, world: World):
        cfg: ScenarioConfig = world.config
        self.cfg = cfg
        self.geo_mode = cfg.geo.mode
        self.n_wh = world.n_wh
        self.n_sku = world.n_sku
        self.wh_x = world.warehouses["x"].to_numpy(np.float64)
        self.wh_y = world.warehouses["y"].to_numpy(np.float64)
        self.wh_rank = world.warehouses["rank"].to_numpy(np.float64)
        self.handling = world.warehouses["handling_cost_per_line"].to_numpy(np.float64)
        self.capacity_lines = world.warehouses["capacity_lines_per_day"].to_numpy(np.int64)
        self.carrier_parcels = world.warehouses["carrier_parcels_per_day"].to_numpy(np.int64)
        self.storage_cost = world.warehouses["storage_cost_per_unit_day"].to_numpy(np.float64)
        self.cutoff_min = world.warehouses["cutoff_min"].to_numpy(np.int64)
        self.sku_weight = world.skus.sort_values("sku_id")["weight_kg"].to_numpy(np.float64)
        self.sku_price = world.skus.sort_values("sku_id")["price"].to_numpy(np.float64)
        self.sku_category = world.skus.sort_values("sku_id")["category"].to_numpy(np.int64)
        self.sku_popularity_rank = (
            world.skus.sort_values("sku_id")["popularity_rank"].to_numpy(np.int64)
        )
        self.assortment = world.assortment  # [n_sku, n_wh] uint8
        self.shipping = ShippingModel(cfg.tariffs)

    def distance_to(self, dest_x: float, dest_y: float) -> np.ndarray:
        """Distance (km) from each warehouse to the delivery point."""
        if self.geo_mode == "plane":
            return np.sqrt((self.wh_x - dest_x) ** 2 + (self.wh_y - dest_y) ** 2)
        from .geometry import distance_km

        return distance_km(
            self.wh_x, self.wh_y, np.full(self.n_wh, dest_x), np.full(self.n_wh, dest_y), "haversine"
        )
