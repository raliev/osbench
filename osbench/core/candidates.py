"""Candidate engine: build the set of candidate warehouses and enumerate assignments.

Reused by all sourcing policies. Vectorized on numpy (njit is an extension
point). Physical stock is not used — only ATP (book − reserved − safety).
"""

from __future__ import annotations

from itertools import combinations

import numpy as np

from ..types import CandidateSet, LineAlloc
from .network import Network


def build_candidate_set(
    order_id: int,
    line_ids: np.ndarray,
    line_sku: np.ndarray,
    line_qty: np.ndarray,
    dest_x: float,
    dest_y: float,
    now_day: int,
    net: Network,
    atp: np.ndarray,
    promise_day: int | None = None,
) -> CandidateSet:
    """Build candidates for an order.

    atp: [n_sku, n_wh] available-to-promise stock (reserved and safety stock accounted for).
    If atp.tolerance is enabled, warehouses outside the price/time tolerance are dropped.
    """
    n_wh = net.n_wh
    L = line_ids.shape[0]

    # cover_full[l, w] — how many units of line l warehouse w covers (considering assortment).
    atp_sub = atp[line_sku, :]  # [L, n_wh]
    assort = net.assortment[line_sku, :]  # [L, n_wh]
    avail = np.where(assort > 0, atp_sub, 0)
    cover_full = np.minimum(avail, line_qty[:, None])  # [L, n_wh]

    has_any = (cover_full > 0).any(axis=0)  # warehouse covers at least one line
    wh_ids = np.nonzero(has_any)[0].astype(np.int32)
    if wh_ids.shape[0] == 0:
        return _empty_candidates(order_id, line_ids, line_sku, line_qty)

    dist = net.distance_to(dest_x, dest_y)[wh_ids]
    zone = net.shipping.zone(dist)
    weight_sum = float(net.sku_weight[line_sku].sum())
    ship_cost = net.shipping.parcel_cost(zone, weight_sum).astype(np.float32)
    ship_days = net.shipping.transit_days(zone).astype(np.int32)

    # ATP network tolerance: keep only warehouses within price/time of the best option
    # (always keep the cheapest, so a line is never dropped purely by tolerance).
    tol = net.cfg.atp
    if tol.tolerance_enabled and wh_ids.shape[0] > 1:
        cheapest = float(ship_cost.min())
        keep = ship_cost <= cheapest * (1.0 + tol.tolerance_price_frac) + 1e-6
        if promise_day is not None:
            on_time = (now_day + ship_days) <= (promise_day + tol.tolerance_days)
            keep = keep & on_time
        if not keep.any():
            keep = np.zeros(wh_ids.shape[0], bool)
            keep[int(np.argmin(ship_cost))] = True
        wh_ids = wh_ids[keep]
        ship_cost = ship_cost[keep]
        ship_days = ship_days[keep]
        dist = dist[keep]

    cover = cover_full[:, wh_ids].T.astype(np.int32)  # [C, L]
    full_cover_mask = (cover == line_qty[None, :]).all(axis=1)
    handling = (net.handling[wh_ids] * L).astype(np.float32)

    return CandidateSet(
        order_id=order_id,
        wh_ids=wh_ids,
        cover=cover,
        full_cover_mask=full_cover_mask,
        ship_cost=ship_cost,
        ship_days=ship_days,
        handling=handling,
        distance_km=dist.astype(np.float32),
        line_ids=line_ids.astype(np.int32),
        line_sku=line_sku.astype(np.int32),
        line_qty=line_qty.astype(np.int32),
    )


def _empty_candidates(order_id, line_ids, line_sku, line_qty) -> CandidateSet:
    return CandidateSet(
        order_id=order_id,
        wh_ids=np.empty(0, np.int32),
        cover=np.empty((0, line_ids.shape[0]), np.int32),
        full_cover_mask=np.empty(0, bool),
        ship_cost=np.empty(0, np.float32),
        ship_days=np.empty(0, np.int32),
        handling=np.empty(0, np.float32),
        distance_km=np.empty(0, np.float32),
        line_ids=line_ids.astype(np.int32),
        line_sku=line_sku.astype(np.int32),
        line_qty=line_qty.astype(np.int32),
    )


def assign_subset(cs: CandidateSet, subset: np.ndarray) -> tuple[dict[int, int], set[int], float]:
    """Greedily assign lines to warehouses from subset (indices into cs arrays).

    Returns (line_pos→cand_idx, uncovered line_pos, shipping+handling cost).
    For each line we pick the lowest-cost warehouse from subset that covers it.
    """
    assigned: dict[int, int] = {}
    unassigned: set[int] = set()
    used_wh: set[int] = set()
    for l in range(cs.n_lines):
        best_c = -1
        best_cost = np.inf
        for c in subset:
            if cs.cover[c, l] >= cs.line_qty[l]:
                cost = cs.ship_cost[c] + cs.handling[c]
                if cost < best_cost:
                    best_cost = cost
                    best_c = int(c)
        if best_c < 0:
            unassigned.add(l)
        else:
            assigned[l] = best_c
            used_wh.add(best_c)
    cost = sum(float(cs.ship_cost[c]) for c in used_wh) + sum(
        float(cs.handling[c]) for c in used_wh
    )
    return assigned, unassigned, cost


def enumerate_assignments(
    cs: CandidateSet, topk: int = 4, split_penalty_fixed: float = 1.0
) -> tuple[dict[int, int], set[int], float]:
    """Exhaustive search over the top-k warehouses + greedy fill. Minimizes cost + split penalty."""
    if cs.n_candidates == 0:
        return {}, set(range(cs.n_lines)), 0.0

    rank_key = cs.ship_cost + cs.handling
    order = np.argsort(rank_key, kind="stable")
    top = order[: min(topk, cs.n_candidates)]

    best = None
    best_score = np.inf
    for r in range(1, len(top) + 1):
        for subset in combinations(top.tolist(), r):
            sub = np.array(subset, dtype=np.int64)
            assigned, unassigned, cost = assign_subset(cs, sub)
            if unassigned:
                continue  # incomplete coverage — skip at this stage
            n_parcels = len({assigned[l] for l in assigned})
            score = cost + split_penalty_fixed * max(0, n_parcels - 1)
            if score < best_score:
                best_score = score
                best = (assigned, set(), score)
    if best is not None:
        return best

    # No full coverage — greedy fill over all candidates.
    return greedy_fill(cs, split_penalty_fixed)


def greedy_fill(
    cs: CandidateSet, split_penalty_fixed: float = 1.0
) -> tuple[dict[int, int], set[int], float]:
    """Greedily cover as many lines as possible, minimizing marginal cost."""
    assigned: dict[int, int] = {}
    unassigned = set(range(cs.n_lines))
    used_wh: set[int] = set()
    while unassigned:
        best_c = -1
        best_key = -np.inf
        best_lines: list[int] = []
        for c in range(cs.n_candidates):
            covered = [l for l in unassigned if cs.cover[c, l] >= cs.line_qty[l]]
            if not covered:
                continue
            new_parcel = c not in used_wh
            marginal = cs.ship_cost[c] + cs.handling[c] + (
                split_penalty_fixed if new_parcel else 0.0
            )
            key = len(covered) / (marginal + 1e-9)
            if key > best_key:
                best_key = key
                best_c = c
                best_lines = covered
        if best_c < 0:
            break
        for l in best_lines:
            assigned[l] = best_c
            unassigned.discard(l)
        used_wh.add(best_c)
    cost = sum(float(cs.ship_cost[c]) for c in used_wh) + sum(
        float(cs.handling[c]) for c in used_wh
    )
    return assigned, unassigned, cost


def plan_to_assignment(cs: CandidateSet, assigned: dict[int, int], unassigned: set[int], cost: float):
    """Convert a plan (line_pos→cand_idx) into an Assignment."""
    from ..types import Assignment

    allocs = []
    used_wh: set[int] = set()
    for l, c in assigned.items():
        wh = int(cs.wh_ids[c])
        used_wh.add(wh)
        allocs.append(
            LineAlloc(
                line_id=int(cs.line_ids[l]),
                sku_id=int(cs.line_sku[l]),
                warehouse_id=wh,
                qty=int(cs.line_qty[l]),
            )
        )
    ship = sum(float(cs.ship_cost[c]) for c in {assigned[l] for l in assigned})
    handling = sum(float(cs.handling[c]) for c in {assigned[l] for l in assigned})
    return Assignment(
        order_id=cs.order_id,
        lines=tuple(allocs),
        unassigned_lines=tuple(int(cs.line_ids[l]) for l in sorted(unassigned)),
        est_ship_cost=ship,
        est_handling_cost=handling,
    )
