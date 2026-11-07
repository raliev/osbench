"""Initialization of inventory and replenishment levels."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..generate.world import World


@dataclass(slots=True)
class InventoryState:
    """Book/physical stock, reserved, in-transit, and replenishment levels [n_sku, n_wh]."""

    book: np.ndarray
    phys: np.ndarray
    reserved: np.ndarray
    in_transit: np.ndarray
    safety: np.ndarray
    s_level: np.ndarray
    S_level: np.ndarray
    # OMS snapshot of book stock (2.12). Equal to book unless inventory_sync lags it. Policies
    # and ATP read this, never `book` directly, so sync latency is a single switch in the engine.
    snapshot: np.ndarray = None  # type: ignore[assignment]

    def atp(self) -> np.ndarray:
        """ATP basic = book − reserved − safety (>=0)."""
        return np.clip(self.book - self.reserved - self.safety, 0, None)

    def position(self) -> np.ndarray:
        """Inventory position for replenishment = book + in-transit − reserved."""
        return self.book + self.in_transit - self.reserved


def sku_daily_demand(world: World) -> np.ndarray:
    """Estimate of expected daily demand (units) for each SKU."""
    cfg = world.config
    n = world.n_sku
    ranks = world.skus.sort_values("sku_id")["popularity_rank"].to_numpy()
    pmf = (ranks.astype(np.float64) + 1.0) ** (-cfg.sku.zipf_a)
    pmf /= pmf.sum()
    daily_lines = cfg.demand.base_orders_per_day * cfg.demand.lines_per_order_mean
    qty_mean = (1 + cfg.demand.qty_max) / 2.0
    return daily_lines * pmf * qty_mean  # [n_sku]


def init_inventory(
    world: World, avg_lead_days: float, review_days: int,
    safety_days: float = 0.5, S_overshoot: float = 1.5,
) -> InventoryState:
    """Initial stock covers initial_cover_days; S/s levels based on demand and lead time.

    safety_days — safety stock in days of demand. S_overshoot — order-up-to level as a multiple
    of (lead + review) days of demand.
    """
    cfg = world.config
    n_sku, n_wh = world.n_sku, world.n_wh
    assort = world.assortment.astype(np.float64)  # [n_sku, n_wh]
    n_stock = np.clip(assort.sum(axis=1), 1, None)  # warehouses stocking the SKU

    demand = sku_daily_demand(world)  # [n_sku]
    demand_per_wh = (demand / n_stock)[:, None] * assort  # [n_sku, n_wh], 0 where no assortment

    book = np.ceil(demand_per_wh * cfg.initial_cover_days).astype(np.int64)
    safety = np.ceil(demand_per_wh * safety_days).astype(np.int64)
    s_level = np.ceil(demand_per_wh * avg_lead_days).astype(np.int64)
    S_level = np.ceil(demand_per_wh * (avg_lead_days + review_days) * S_overshoot).astype(np.int64)

    return InventoryState(
        book=book,
        phys=book.copy(),
        reserved=np.zeros((n_sku, n_wh), np.int64),
        in_transit=np.zeros((n_sku, n_wh), np.int64),
        safety=safety,
        s_level=s_level,
        S_level=S_level,
        snapshot=book.copy(),
    )
