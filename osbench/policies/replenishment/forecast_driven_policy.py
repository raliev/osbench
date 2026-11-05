"""forecast_driven replenishment: set the order-up-to level S from a demand forecast over the
lead-time + review window plus an explicit safety stock z·σ (a realistic industrial scheme).

The forecast is the realized demand rate per (sku, warehouse) observed so far (``demand_rate``);
σ is approximated from a Poisson assumption (σ = sqrt(mean over the window)). Before any demand
is observed it falls back to the static S level from the generator.
"""

from __future__ import annotations

import numpy as np

from ...config.policy import ReplenishmentPolicyConfig
from ...core.network import Network
from ...registry import register_replenishment


@register_replenishment("forecast_driven")
class ForecastDrivenPolicy:
    name = "forecast_driven"

    def __init__(self, cfg: ReplenishmentPolicyConfig, net: Network):
        self.cfg = cfg
        self.net = net
        self.z = float(cfg.params.get("service_z", 1.645))  # z(0.95) safety factor
        lead = float(np.mean([s.lead_time_days for s in net.cfg.suppliers]))
        self._coverage = lead + cfg.review_days  # days of demand the order-up-to level covers

    def order_quantities(
        self, position: np.ndarray, s_level: np.ndarray, S_level: np.ndarray,
        demand_rate: np.ndarray | None = None,
    ) -> np.ndarray:
        if demand_rate is None or not demand_rate.any():
            return np.clip(S_level - position, 0, None).astype(np.int64)
        mean = demand_rate * self._coverage                  # expected demand over the window
        safety = self.z * np.sqrt(np.clip(mean, 0, None))    # Poisson-normal safety stock
        S = np.ceil(mean + safety).astype(np.int64)
        return np.clip(S - position, 0, None).astype(np.int64)
