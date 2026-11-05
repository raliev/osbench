"""nearest policy — the closest warehouse covering all lines; otherwise minimize parcels."""

from __future__ import annotations

import numpy as np

from ...config.policy import SourcingPolicyConfig
from ...core.candidates import greedy_fill, plan_to_assignment
from ...core.network import Network
from ...registry import register_sourcing
from ...types import Assignment, Decision, Order, PolicyContext
from ..base import candidates_for


@register_sourcing("nearest")
class NearestPolicy:
    name = "nearest"

    def __init__(self, cfg: SourcingPolicyConfig, net: Network):
        self.cfg = cfg
        self.net = net

    def decide(self, order: Order, ctx: PolicyContext) -> Decision:
        cs = candidates_for(order, ctx, self.net)
        if cs.n_candidates == 0:
            return Assignment(order_id=order.order_id, lines=(), unassigned_lines=tuple(
                ln.line_id for ln in order.lines
            ))
        full = np.nonzero(cs.full_cover_mask)[0]
        if full.size > 0:
            c = int(full[np.argmin(cs.distance_km[full])])
            assigned = {l: c for l in range(cs.n_lines)}
            return plan_to_assignment(cs, assigned, set(), 0.0)
        # No full coverage: greedily minimize the number of parcels by proximity.
        assigned, unassigned, cost = greedy_fill(cs, split_penalty_fixed=1e6)
        return plan_to_assignment(cs, assigned, unassigned, cost)

    def on_period_start(self, ctx: PolicyContext) -> None:
        pass

    def fit(self, history: object) -> None:
        pass
