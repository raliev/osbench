"""Shared LP machinery for dual-price (bid-price) sourcing policies.

Per SKU we solve a small transportation LP (warehouses -> regions) on forecast demand:

    min  sum_{w,r} cost[w,r] * x[w,r] + penalty * sum_r unmet[r]
    s.t. sum_r x[w,r]        <= supply[w]      (dual u[w] <= 0)
         sum_w x[w,r] + unmet[r] = demand[r]
         x, unmet >= 0

The marginal of each supply constraint is the opportunity cost of one unit at that
warehouse; the bid price is bid[w] = -u[w] (a scarcer warehouse earns a higher bid).
"""

from __future__ import annotations

from itertools import combinations

import numpy as np
from scipy.optimize import linprog

from ...types import CandidateSet


def assign_with_bids(
    cs: CandidateSet, bid: np.ndarray, topk: int = 4, split_penalty_fixed: float = 1.0
) -> tuple[dict[int, int], set[int], float]:
    """Assign order lines minimizing per-warehouse shipping + bid cost + split penalty.

    bid: [n_sku, n_wh] opportunity cost per unit. Shipping is charged once per used
    warehouse (not per line), so consolidation is rewarded. Returns (line->cand, unassigned, cost).
    """
    C, L = cs.n_candidates, cs.n_lines
    if C == 0:
        return {}, set(range(L)), 0.0

    # Per (candidate, line) bid cost; inf where the candidate cannot cover the line.
    bidcost = np.full((C, L), np.inf)
    for c in range(C):
        wh = int(cs.wh_ids[c])
        for l in range(L):
            if cs.cover[c, l] >= cs.line_qty[l]:
                bidcost[c, l] = bid[int(cs.line_sku[l]), wh] * int(cs.line_qty[l])

    rank = cs.ship_cost + cs.handling
    top = np.argsort(rank, kind="stable")[: min(topk, C)]

    best, best_score = None, np.inf
    for r in range(1, len(top) + 1):
        for subset in combinations(top.tolist(), r):
            sub = list(subset)
            assigned: dict[int, int] = {}
            ok = True
            for l in range(L):
                opts = [(bidcost[c, l], c) for c in sub if np.isfinite(bidcost[c, l])]
                if not opts:
                    ok = False
                    break
                assigned[l] = min(opts)[1]
            if not ok:
                continue
            used = {assigned[l] for l in assigned}
            cost = (
                sum(float(cs.ship_cost[c] + cs.handling[c]) for c in used)
                + sum(float(bidcost[assigned[l], l]) for l in assigned)
                + split_penalty_fixed * (len(used) - 1)
            )
            if cost < best_score:
                best_score, best = cost, (dict(assigned), set(), cost)
    if best is not None:
        return best

    # No fully-covering subset: greedy line-by-line over all candidates.
    assigned, unassigned, used = {}, set(), set()
    for l in range(L):
        opts = [(bidcost[c, l], c) for c in range(C) if np.isfinite(bidcost[c, l])]
        if not opts:
            unassigned.add(l)
        else:
            c = min(opts)[1]
            assigned[l] = c
            used.add(c)
    cost = sum(float(cs.ship_cost[c] + cs.handling[c]) for c in used)
    return assigned, unassigned, cost


def solve_bid_prices(
    supply: np.ndarray, demand: np.ndarray, cost: np.ndarray, unmet_penalty: float
) -> np.ndarray:
    """Return bid prices per warehouse for one SKU.

    supply: [n_wh], demand: [n_region], cost: [n_wh, n_region]. Returns [n_wh] >= 0.
    """
    n_wh, n_reg = cost.shape
    if supply.sum() <= 0 or demand.sum() <= 0:
        return np.zeros(n_wh)

    n_x = n_wh * n_reg
    # Variables: x[w,r] flattened row-major, then unmet[r].
    c = np.concatenate([cost.reshape(-1), np.full(n_reg, unmet_penalty)])

    # Supply constraints: for each w, sum_r x[w,r] <= supply[w].
    A_ub = np.zeros((n_wh, n_x + n_reg))
    for w in range(n_wh):
        A_ub[w, w * n_reg : (w + 1) * n_reg] = 1.0
    b_ub = supply.astype(float)

    # Demand constraints: for each r, sum_w x[w,r] + unmet[r] = demand[r].
    A_eq = np.zeros((n_reg, n_x + n_reg))
    for r in range(n_reg):
        for w in range(n_wh):
            A_eq[r, w * n_reg + r] = 1.0
        A_eq[r, n_x + r] = 1.0
    b_eq = demand.astype(float)

    res = linprog(
        c, A_ub=A_ub, b_ub=b_ub, A_eq=A_eq, b_eq=b_eq,
        bounds=[(0, None)] * (n_x + n_reg), method="highs",
    )
    if not res.success or res.ineqlin is None:
        return np.zeros(n_wh)
    # Marginals of <= supply constraints are <= 0; bid = -marginal >= 0.
    return np.clip(-np.asarray(res.ineqlin.marginals), 0, None)
