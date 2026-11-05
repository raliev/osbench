"""(s, S) replenishment policy: if the position < s, order up to S."""

from __future__ import annotations

import numpy as np

from ...config.policy import ReplenishmentPolicyConfig
from ...core.network import Network
from ...registry import register_replenishment


@register_replenishment("s_S")
class SsSPolicy:
    name = "s_S"

    def __init__(self, cfg: ReplenishmentPolicyConfig, net: Network):
        self.cfg = cfg
        self.net = net

    def order_quantities(
        self, position: np.ndarray, s_level: np.ndarray, S_level: np.ndarray,
        demand_rate: np.ndarray | None = None,
    ) -> np.ndarray:
        below = position < s_level
        qty = np.where(below, S_level - position, 0)
        return np.clip(qty, 0, None).astype(np.int64)
