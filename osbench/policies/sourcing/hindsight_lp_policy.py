"""hindsight_lp — an (upper-bound) sourcing policy that sees the whole realized demand and
plans capacity-aware region→warehouse flows offline, then follows them online.

Before the run, a transportation LP minimises total shipping cost of serving each region's
realized demand subject to each warehouse's throughput capacity over the horizon. The primal
flows give, per region, a preference order of warehouses that balances load the way an
omniscient planner would. Online, each order is placed at its region's most-preferred warehouse
with stock (full cover), else filled greedily by preference. It is a planning bound, not an
exact per-order optimum, so real policies can be measured against it.
"""

from __future__ import annotations

import numpy as np
from scipy.optimize import linprog

from ...config.policy import SourcingPolicyConfig
from ...core.candidates import plan_to_assignment
from ...core.network import Network
from ...registry import register_sourcing
from ...types import Assignment, Decision, Order, PolicyContext
from ..base import candidates_for


@register_sourcing("hindsight_lp")
class HindsightLPPolicy:
    name = "hindsight_lp"

    def __init__(self, cfg: SourcingPolicyConfig, net: Network):
        self.cfg = cfg
        self.net = net
        self._pref: np.ndarray | None = None  # [n_region, n_wh] warehouse preference order

    def _region_cost(self) -> np.ndarray:
        """Shipping cost per (warehouse, region centroid)."""
        net = self.net
        cost = np.zeros((net.n_wh, len(net.cfg.regions)))
        for ri, r in enumerate(net.cfg.regions):
            zone = net.shipping.zone(net.distance_to(r.x, r.y))
            cost[:, ri] = net.shipping.cost[zone]
        return cost

    def _solve_pref(self, demand: np.ndarray, cap: np.ndarray, cost: np.ndarray) -> np.ndarray:
        """Capacity-constrained transportation LP region->warehouse; return per-region warehouse
        preference order (by descending planned flow, ties by cheaper cost)."""
        n_wh, n_reg = cost.shape
        if demand.sum() <= 0:
            return np.argsort(cost, axis=0).T
        if cap.sum() < demand.sum():  # keep the LP feasible
            cap = cap * (demand.sum() / cap.sum() + 1e-6)
        A_ub = np.zeros((n_wh, n_wh * n_reg))
        for w in range(n_wh):
            A_ub[w, w * n_reg:(w + 1) * n_reg] = 1.0
        A_eq = np.zeros((n_reg, n_wh * n_reg))
        for rr in range(n_reg):
            A_eq[rr, rr::n_reg] = 1.0
        res = linprog(cost.reshape(-1), A_ub=A_ub, b_ub=cap, A_eq=A_eq, b_eq=demand,
                      bounds=[(0, None)] * (n_wh * n_reg), method="highs")
        if not res.success:
            return np.argsort(cost, axis=0).T
        flow = res.x.reshape(n_wh, n_reg)
        return np.array(
            [sorted(range(n_wh), key=lambda w: (-flow[w, rr], cost[w, rr])) for rr in range(n_reg)]
        )

    def plan(self, engine) -> None:
        # Realized demand (units) per region from the whole order stream (the hindsight part).
        demand = np.zeros(len(self.net.cfg.regions), dtype=np.float64)
        np.add.at(demand, engine.o_region.astype(np.int64),
                  [int(engine.l_qty[engine.o_start[o]:engine.o_end[o]].sum())
                   for o in range(engine.n_orders)])
        cap = self.net.capacity_lines.astype(np.float64) * max(1, self.net.cfg.sim_days)
        self._pref = self._solve_pref(demand, cap, self._region_cost())

    def decide(self, order: Order, ctx: PolicyContext) -> Decision:
        cs = candidates_for(order, ctx, self.net)
        if cs.n_candidates == 0:
            return Assignment(order_id=order.order_id, lines=(), unassigned_lines=tuple(
                ln.line_id for ln in order.lines))
        pref = self._pref[order.region_id] if self._pref is not None else cs.wh_ids
        cand_index = {int(wh): c for c, wh in enumerate(cs.wh_ids)}
        ranked = [cand_index[int(w)] for w in pref if int(w) in cand_index]

        # Prefer a single warehouse (highest region preference) that fully covers the order.
        for c in ranked:
            if cs.full_cover_mask[c]:
                assigned = {l: c for l in range(cs.n_lines)}
                return plan_to_assignment(cs, assigned, set(), 0.0)
        # Otherwise fill each line by region preference order.
        assigned: dict[int, int] = {}
        unassigned: set[int] = set()
        for l in range(cs.n_lines):
            for c in ranked:
                if cs.cover[c, l] >= cs.line_qty[l]:
                    assigned[l] = c
                    break
            else:
                unassigned.add(l)
        return plan_to_assignment(cs, assigned, unassigned, 0.0)

    def on_period_start(self, ctx: PolicyContext) -> None:
        pass

    def fit(self, history: object) -> None:
        pass
