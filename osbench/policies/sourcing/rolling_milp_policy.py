"""rolling_milp — periodic capacity-constrained re-optimization (analogous to Xu et al. 2009).

Every ``period_days`` a transportation LP re-plans region→warehouse flows for the demand
expected in the coming window, subject to each warehouse's throughput capacity over that window.
Online, orders follow the freshly planned per-region warehouse preferences. Unlike hindsight_lp
it uses a rolling forecast (the demand model), not the realized future — a tractable LP-relaxation
stand-in for a full batched MILP.
"""

from __future__ import annotations

import numpy as np

from ...config.policy import SourcingPolicyConfig
from ...core.network import Network
from ...registry import register_sourcing
from ...types import PolicyContext
from .hindsight_lp_policy import HindsightLPPolicy


@register_sourcing("rolling_milp")
class RollingMILPPolicy(HindsightLPPolicy):
    name = "rolling_milp"

    def __init__(self, cfg: SourcingPolicyConfig, net: Network):
        super().__init__(cfg, net)
        self.period_days = int(cfg.params.get("period_days", 1))  # re-optimize cadence
        w = np.array([r.population_weight for r in net.cfg.regions], dtype=np.float64)
        self._region_share = w / w.sum()
        d = net.cfg.demand
        self._units_per_day = d.base_orders_per_day * d.lines_per_order_mean * (1 + d.qty_max) / 2.0
        self._cost = self._region_cost()
        self._replan()  # initial plan before day 0

    def _replan(self) -> None:
        demand = self._region_share * self._units_per_day * self.period_days
        cap = self.net.capacity_lines.astype(np.float64) * self.period_days
        self._pref = self._solve_pref(demand, cap, self._cost)

    def plan(self, engine) -> None:
        # rolling_milp forecasts rather than using hindsight; nothing to precompute from the stream.
        pass

    def on_period_start(self, ctx: PolicyContext) -> None:
        self._replan()
