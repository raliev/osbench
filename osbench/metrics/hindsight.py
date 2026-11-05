"""Hindsight (offline) cost lower bound and gap_to_hindsight.

With the full realized order stream known, a per-SKU transportation LP routes demand
(aggregated by region) from warehouses to regions at minimum shipping cost, subject to
each warehouse's share of inventory. The objective is a lower bound on the shipping cost
any online sourcing policy can achieve on the same orders — the "cost of myopia" reference.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import polars as pl
from scipy.optimize import linprog

from ..core.network import Network
from ..core.state import init_inventory
from ..generate import load_world_from_dir

_MIN_PER_DAY = 1440


def _region_ship_cost(net: Network, regions) -> np.ndarray:
    """Per-(warehouse, region) unit shipping cost to region centroids. [n_wh, n_region]."""
    cost = np.zeros((net.n_wh, len(regions)), dtype=np.float64)
    for ri, r in enumerate(regions.iter_rows(named=True)):
        dist = net.distance_to(r["x"], r["y"])
        zone = net.shipping.zone(dist)
        cost[:, ri] = net.shipping.cost[zone]
    return cost


def _transport_min_cost(supply: np.ndarray, demand: np.ndarray, cost: np.ndarray) -> float:
    """Minimum-cost transportation objective (supply and demand totals are made equal)."""
    n_wh, n_reg = cost.shape
    c = cost.reshape(-1)
    # sum_r x[w,r] <= supply[w]
    A_ub = np.zeros((n_wh, n_wh * n_reg))
    for w in range(n_wh):
        A_ub[w, w * n_reg:(w + 1) * n_reg] = 1.0
    # sum_w x[w,r] == demand[r]
    A_eq = np.zeros((n_reg, n_wh * n_reg))
    for r in range(n_reg):
        for w in range(n_wh):
            A_eq[r, w * n_reg + r] = 1.0
    res = linprog(
        c, A_ub=A_ub, b_ub=supply.astype(float), A_eq=A_eq, b_eq=demand.astype(float),
        bounds=[(0, None)] * (n_wh * n_reg), method="highs",
    )
    return float(res.fun) if res.success else float("nan")


def hindsight_cost(scenario_dir: str | Path, warmup_days: int = 0) -> dict:
    """Compute the hindsight shipping lower bound and per-order cost for a scenario.

    Shipping is charged per parcel. The best any policy can do is ship each order as a
    single parcel from its cheapest reachable warehouse, subject to each warehouse's share
    of total inventory. We route orders (not units) region->warehouse to minimize total
    parcel shipping cost — a lower bound that ignores splits and inventory timing.
    """
    scenario_dir = Path(scenario_dir)
    world = load_world_from_dir(scenario_dir)
    net = Network(world)
    n_wh = net.n_wh

    orders = pl.read_parquet(scenario_dir / "orders.parquet").filter(
        pl.col("created_min") >= warmup_days * _MIN_PER_DAY
    )
    n_orders = orders.select(pl.col("order_id").n_unique()).item()
    if n_orders == 0:
        return {"hindsight_shipping": 0.0, "hindsight_per_order": None, "n_orders": 0}

    cost = _region_ship_cost(net, pl.from_pandas(world.regions))  # [n_wh, n_region]

    # Orders per region (one parcel each).
    per_region = (
        orders.group_by("order_id").agg(pl.col("region_id").first())
        .group_by("region_id").len().sort("region_id")
    )
    demand = np.zeros(cost.shape[1])
    for row in per_region.iter_rows(named=True):
        demand[int(row["region_id"])] = float(row["len"])

    # Warehouse supply share from the aggregate initial inventory.
    avg_lead = float(np.mean([s.lead_time_days for s in net.cfg.suppliers]))
    inv = init_inventory(world, avg_lead, 7)
    frac = inv.book.sum(axis=0).astype(float)  # total units per warehouse
    if frac.sum() <= 0:
        frac = np.ones(n_wh)
    supply = demand.sum() * frac / frac.sum()

    shipping_total = _transport_min_cost(supply, demand, cost)

    n_lines = orders.height
    handling_bound = n_lines * float(net.handling.min())
    per_order = (shipping_total + handling_bound) / n_orders
    return {
        "hindsight_shipping": round(shipping_total, 4),
        "hindsight_per_order": round(per_order, 4),
        "n_orders": int(n_orders),
    }


def gap_to_hindsight(shipping_cost_per_order: float, hindsight_per_order: float) -> float | None:
    """(policy_shipping_per_order - hindsight_per_order) / hindsight_per_order."""
    if not hindsight_per_order or hindsight_per_order <= 0:
        return None
    return (shipping_cost_per_order - hindsight_per_order) / hindsight_per_order
