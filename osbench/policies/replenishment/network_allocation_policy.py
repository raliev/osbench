"""network_allocation replenishment (Acimovic & Graves 2017): decide a network order per SKU,
then allocate it across warehouses in proportion to each warehouse's observed demand share.

This couples replenishment to the sourcing pattern — stock is pushed to the warehouses that
actually serve the demand, rather than sized per cell in isolation (as in base_stock). The
network order-up-to level mirrors forecast_driven (lead+review coverage + z·σ safety).
"""

from __future__ import annotations

import numpy as np

from ...config.policy import ReplenishmentPolicyConfig
from ...core.network import Network
from ...registry import register_replenishment


@register_replenishment("network_allocation")
class NetworkAllocationPolicy:
    name = "network_allocation"

    def __init__(self, cfg: ReplenishmentPolicyConfig, net: Network):
        self.cfg = cfg
        self.net = net
        self.z = float(cfg.params.get("service_z", 1.645))
        lead = float(np.mean([s.lead_time_days for s in net.cfg.suppliers]))
        self._coverage = lead + cfg.review_days

    def order_quantities(
        self, position: np.ndarray, s_level: np.ndarray, S_level: np.ndarray,
        demand_rate: np.ndarray | None = None,
    ) -> np.ndarray:
        if demand_rate is None or not demand_rate.any():
            return np.clip(S_level - position, 0, None).astype(np.int64)
        rate_sku = demand_rate.sum(axis=1)                        # [n_sku] network demand/day
        mean = rate_sku * self._coverage
        S_net = mean + self.z * np.sqrt(np.clip(mean, 0, None))   # network order-up-to per SKU
        pos_sku = position.sum(axis=1)
        order_sku = np.clip(S_net - pos_sku, 0, None)             # [n_sku] total to buy
        # Allocate pro rata to warehouses by their demand share (fall back to assortment split).
        share = demand_rate / np.clip(demand_rate.sum(axis=1, keepdims=True), 1e-9, None)
        no_demand = demand_rate.sum(axis=1) <= 0
        if no_demand.any():
            assort = (self.net.assortment > 0).astype(np.float64)
            assort_share = assort / np.clip(assort.sum(axis=1, keepdims=True), 1e-9, None)
            share[no_demand] = assort_share[no_demand]
        qty = order_sku[:, None] * share
        return np.floor(qty).astype(np.int64)
