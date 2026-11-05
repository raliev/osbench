"""base_stock replenishment policy: raise the inventory position up to level S.

Inventory position = on-hand book stock + in-transit − reserved. Reviewed every R days (scheduled by the core).
The engine passes the target S levels (computed from demand and lead time).
"""

from __future__ import annotations

import numpy as np

from ...config.policy import ReplenishmentPolicyConfig
from ...core.network import Network
from ...registry import register_replenishment


@register_replenishment("base_stock")
class BaseStockPolicy:
    name = "base_stock"

    def __init__(self, cfg: ReplenishmentPolicyConfig, net: Network):
        self.cfg = cfg
        self.net = net

    def order_quantities(
        self, position: np.ndarray, s_level: np.ndarray, S_level: np.ndarray,
        demand_rate: np.ndarray | None = None,
    ) -> np.ndarray:
        """Return the desired purchase quantity per [n_sku, n_wh]."""
        return np.clip(S_level - position, 0, None).astype(np.int64)
