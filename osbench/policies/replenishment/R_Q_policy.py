"""R_Q replenishment: review every R days; if the position is below the reorder point,
order fixed lots of Q (as in Wang & Minner 2024). Q defaults to the (S - s) band and is
ordered in whole multiples to clear the reorder point, giving batchy, lumpy orders.
"""

from __future__ import annotations

import numpy as np

from ...config.policy import ReplenishmentPolicyConfig
from ...core.network import Network
from ...registry import register_replenishment


@register_replenishment("R_Q")
class RQPolicy:
    name = "R_Q"

    def __init__(self, cfg: ReplenishmentPolicyConfig, net: Network):
        self.cfg = cfg
        self.net = net
        self.q_mult = float(cfg.params.get("q_mult", 1.0))  # scales the fixed lot size Q

    def order_quantities(
        self, position: np.ndarray, s_level: np.ndarray, S_level: np.ndarray,
        demand_rate: np.ndarray | None = None,
    ) -> np.ndarray:
        reorder = s_level
        Q = np.maximum(1, np.ceil((S_level - s_level) * self.q_mult)).astype(np.int64)
        below = position < reorder
        # Whole multiples of Q to lift the position back above the reorder point.
        gap = np.clip(reorder - position, 0, None)
        lots = np.ceil(gap / Q).astype(np.int64)
        qty = np.where(below, lots * Q, 0)
        return np.clip(qty, 0, None).astype(np.int64)
