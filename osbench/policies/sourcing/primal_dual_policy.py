"""primal_dual: online dual multipliers without a forecast (Andrews et al. 2019).

No LP and no forecast: a warehouse's bid grows as its inventory depletes relative to the
level first observed. Each line is assigned to the candidate warehouse minimizing
ship_cost + bid * qty, so draining a warehouse makes it progressively less attractive.
"""

from __future__ import annotations

import numpy as np

from ...config.policy import SourcingPolicyConfig
from ...core.candidates import plan_to_assignment
from ...core.network import Network
from ...registry import register_sourcing
from ...types import Assignment, Decision, Order, PolicyContext
from ..base import candidates_for
from .lp_common import assign_with_bids


@register_sourcing("primal_dual")
class PrimalDualPolicy:
    name = "primal_dual"

    def __init__(self, cfg: SourcingPolicyConfig, net: Network):
        self.cfg = cfg
        self.net = net
        self.k = float(cfg.params.get("depletion_k", 3.0))  # steepness of the bid curve
        self._ref: np.ndarray | None = None  # reference inventory (first seen)
        # Bid scale ~ typical shipping cost so bid and ship_cost are comparable.
        self._scale = float(np.median(net.shipping.cost))

    def _bid(self, atp: np.ndarray) -> np.ndarray:
        if self._ref is None:
            self._ref = np.clip(atp.astype(np.float64), 1.0, None)
        frac = np.clip(atp.astype(np.float64) / self._ref, 0.0, 1.0)
        # Depleted (frac -> 0) yields a high bid; full (frac = 1) yields ~0.
        return self._scale * (np.exp(self.k * (1.0 - frac)) - 1.0)

    def decide(self, order: Order, ctx: PolicyContext) -> Decision:
        cs = candidates_for(order, ctx, self.net)
        if cs.n_candidates == 0:
            return Assignment(
                order_id=order.order_id, lines=(),
                unassigned_lines=tuple(ln.line_id for ln in order.lines),
            )
        bid = self._bid(ctx.atp)
        assigned, unassigned, cost = assign_with_bids(
            cs, bid, self.cfg.topk_warehouses, self.cfg.split_penalty_fixed
        )
        return plan_to_assignment(cs, assigned, unassigned, cost)

    def fit(self, history: object) -> None:
        pass
