"""spillover_limit (DeValve et al. 2023): myopic cheapest sourcing, but each warehouse accepts
only a limited share of "foreign" orders per day — orders whose nearest warehouse is a different
one. Once a warehouse's daily spillover budget is used up, routing a foreign order there is
heavily penalised, so it is kept for its home region's demand.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from ...config.policy import SourcingPolicyConfig
from ...core.candidates import enumerate_assignments, plan_to_assignment
from ...core.network import Network
from ...registry import register_sourcing
from ...types import Assignment, Decision, Order, PolicyContext
from ..base import candidates_for

_BIG = 1e6  # soft penalty: discourage over-budget spillover without ever losing the order


@register_sourcing("spillover_limit")
class SpilloverLimitPolicy:
    name = "spillover_limit"

    def __init__(self, cfg: SourcingPolicyConfig, net: Network):
        self.cfg = cfg
        self.net = net
        frac = float(cfg.params.get("spillover_frac", 0.3))
        self._budget = np.maximum(1, np.floor(net.capacity_lines * frac)).astype(np.int64)
        self._day = -1
        self._foreign_used = np.zeros(net.n_wh, np.int64)

    def decide(self, order: Order, ctx: PolicyContext) -> Decision:
        cs = candidates_for(order, ctx, self.net)
        if cs.n_candidates == 0:
            return Assignment(order_id=order.order_id, lines=(), unassigned_lines=tuple(
                ln.line_id for ln in order.lines))
        if ctx.now_day != self._day:  # reset daily budgets
            self._day = ctx.now_day
            self._foreign_used[:] = 0

        home = int(cs.wh_ids[int(np.argmin(cs.distance_km))])  # nearest candidate = home warehouse
        # Penalise foreign candidates whose daily spillover budget is spent.
        ship = cs.ship_cost.copy()
        for c in range(cs.n_candidates):
            wh = int(cs.wh_ids[c])
            if wh != home and self._foreign_used[wh] >= self._budget[wh]:
                ship[c] += _BIG
        cs2 = replace(cs, ship_cost=ship)
        assigned, unassigned, _cost = enumerate_assignments(
            cs2, topk=self.cfg.topk_warehouses, split_penalty_fixed=self.cfg.split_penalty_fixed)
        for wh in {int(cs.wh_ids[c]) for c in assigned.values()}:
            if wh != home:
                self._foreign_used[wh] += 1
        return plan_to_assignment(cs, assigned, unassigned, _cost)

    def on_period_start(self, ctx: PolicyContext) -> None:
        pass

    def fit(self, history: object) -> None:
        pass
