"""dtlp_bidprice: dual transportation LP bid prices (Acimovic & Graves 2015).

Every `period_days` a per-SKU transportation LP is solved on forecast demand; the dual
(shadow) price of each warehouse's inventory is the opportunity cost of a unit. Online,
each line is assigned to the candidate warehouse minimizing ship_cost + bid * qty, so a
scarce warehouse (high bid) is avoided unless it is clearly cheaper to ship from.
"""

from __future__ import annotations

import numpy as np

from ...config.policy import SourcingPolicyConfig
from ...core.candidates import plan_to_assignment
from ...core.network import Network
from ...registry import register_sourcing
from ...types import Assignment, Decision, Order, PolicyContext
from ..base import candidates_for
from .lp_common import assign_with_bids, solve_bid_prices


def _expected_daily_demand(net: Network) -> np.ndarray:
    """Expected per-SKU daily demand (units) from the Zipf popularity model."""
    ranks = net.sku_popularity_rank.astype(np.float64)
    pmf = (ranks + 1.0) ** (-net.cfg.sku.zipf_a)
    pmf /= pmf.sum()
    daily_lines = net.cfg.demand.base_orders_per_day * net.cfg.demand.lines_per_order_mean
    qty_mean = (1 + net.cfg.demand.qty_max) / 2.0
    return daily_lines * pmf * qty_mean


@register_sourcing("dtlp_bidprice")
class DTLPBidPricePolicy:
    name = "dtlp_bidprice"

    def __init__(self, cfg: SourcingPolicyConfig, net: Network):
        self.cfg = cfg
        self.net = net
        self.period_days = int(cfg.params.get("period_days", 3))
        # Forecast over the inventory coverage window, not the whole horizon, so bids are
        # ~0 under abundance and only rise under genuine scarcity.
        self.planning_horizon = int(
            cfg.params.get("planning_horizon_days", net.cfg.initial_cover_days)
        )
        # Tolerance mirror: region can only be served from in-tolerance warehouses, so the
        # LP should not route demand to distant/expensive ones. Marked with a large cost.
        self._tol_frac = net.cfg.atp.tolerance_price_frac if net.cfg.atp.tolerance_enabled else None
        self.bid = np.zeros((net.n_sku, net.n_wh), dtype=np.float64)

        # Static per-(warehouse, region) unit shipping cost to region centroids.
        regions = net.cfg.regions
        self._region_w = np.array([r.population_weight for r in regions], dtype=np.float64)
        self._region_w /= self._region_w.sum()
        cost = np.zeros((net.n_wh, len(regions)), dtype=np.float64)
        for ri, r in enumerate(regions):
            dist = net.distance_to(r.x, r.y)
            zone = net.shipping.zone(dist)
            cost[:, ri] = net.shipping.cost[zone]
        # Respect the ATP tolerance in the forecast LP: mark out-of-tolerance (warehouse,
        # region) pairs with a prohibitive cost so the LP treats each region as served only
        # by its reachable warehouses -> duals capture genuine captivity.
        if self._tol_frac is not None:
            cheapest = cost.min(axis=0)  # cheapest wh per region
            far = cost > cheapest[None, :] * (1.0 + self._tol_frac) + 1e-6
            cost = np.where(far, cost.max() * 10.0, cost)
        self._cost = cost
        # Value of serving demand = the lost-sale penalty, so duals reflect the cost of
        # losing a captive sale (not just shipping) -> stronger preservation of scarce stock.
        self._unmet_penalty = float(max(net.cfg.shortage.penalty_lost_sale, cost.max() * 3.0 + 1.0))
        self._daily_demand = _expected_daily_demand(net)

        # Hybrid online term: the periodic forecast bid captures opportunity cost but reacts
        # only every `period_days`; blending in a real-time depletion signal (as in
        # primal_dual) lets DTLP preserve a warehouse the moment it starts draining -- the
        # reactive edge that a reactive scorer (weighted_score.above_safety) otherwise has.
        # Off by default (k=0) -> pure forecast, backward compatible.
        self._online_k = float(cfg.params.get("online_depletion_k", 0.0))
        self._online_scale = float(np.median(net.shipping.cost))
        self._ref_atp: np.ndarray | None = None

    def _online_bid(self, atp: np.ndarray) -> np.ndarray:
        """Per-(sku, wh) real-time depletion surcharge; ~0 at full stock, rising as it drains."""
        if self._online_k <= 0:
            return 0.0
        if self._ref_atp is None:
            self._ref_atp = np.clip(atp.astype(np.float64), 1.0, None)
        frac = np.clip(atp.astype(np.float64) / self._ref_atp, 0.0, 1.0)
        return self._online_scale * (np.exp(self._online_k * (1.0 - frac)) - 1.0)

    def on_period_start(self, ctx: PolicyContext) -> None:
        remaining = max(1, min(self.net.cfg.sim_days - ctx.now_day, self.planning_horizon))
        supply = ctx.atp  # [n_sku, n_wh], available to promise
        self.bid[:] = 0.0
        for sku in range(self.net.n_sku):
            sup = supply[sku]
            if sup.sum() <= 0:
                continue
            total = self._daily_demand[sku] * remaining
            if total <= 0:
                continue
            demand = total * self._region_w
            self.bid[sku] = solve_bid_prices(
                sup.astype(float), demand, self._cost, self._unmet_penalty
            )

    def decide(self, order: Order, ctx: PolicyContext) -> Decision:
        cs = candidates_for(order, ctx, self.net)
        if cs.n_candidates == 0:
            return Assignment(
                order_id=order.order_id, lines=(),
                unassigned_lines=tuple(ln.line_id for ln in order.lines),
            )
        bid = self.bid
        if self._online_k > 0:
            bid = bid + self._online_bid(ctx.atp)
        assigned, unassigned, cost = assign_with_bids(
            cs, bid, self.cfg.topk_warehouses, self.cfg.split_penalty_fixed
        )
        return plan_to_assignment(cs, assigned, unassigned, cost)

    def fit(self, history: object) -> None:
        pass
