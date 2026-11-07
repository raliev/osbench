"""Pre-generation of the order stream over the whole horizon (the key to CRN).

The stream is an inhomogeneous Poisson process: base intensity × yearly seasonality
× weekly profile × intraday profile × shocks. Order composition: number of lines ~ geometric,
SKUs by Zipf (adjusted for the region's category), quantity 1..qty_max.

The result is a flat table of order LINES, sorted by order creation time.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config.schema import ScenarioConfig
from ..rng.draws import geometric_counts, sample_from_cdf, zipf_cdf
from ..rng.streams import RngHub
from .world import World

_MIN_PER_DAY = 1440


def build_orders(world: World, cfg: ScenarioConfig, hub: RngHub) -> pd.DataFrame:
    arr = hub.generator("demand_arrivals")
    basket = hub.generator("demand_basket")

    region_w = world.regions["population_weight"].to_numpy(dtype=np.float64)
    region_w = region_w / region_w.sum()
    region_x = world.regions["x"].to_numpy(dtype=np.float64)
    region_y = world.regions["y"].to_numpy(dtype=np.float64)

    weekly = np.asarray(cfg.demand.weekly_profile, dtype=np.float64)
    intraday = (
        np.asarray(cfg.demand.intraday_profile, dtype=np.float64)
        if cfg.demand.intraday_profile is not None
        else np.ones(24)
    )
    intraday_cdf = np.cumsum(intraday / intraday.sum())

    # SKU popularity by Zipf is set via rank: build a CDF over ranks and map rank→sku.
    sku_by_rank = (
        world.skus.sort_values("popularity_rank")["sku_id"].to_numpy(dtype=np.int64)
    )
    pop_cdf = zipf_cdf(world.n_sku, cfg.sku.zipf_a)

    day_mult = _daily_multipliers(cfg)

    order_rows_region: list[np.ndarray] = []
    order_created_min: list[np.ndarray] = []

    for day in range(cfg.sim_days):
        lam = cfg.demand.base_orders_per_day * day_mult[day] * weekly[day % 7]
        n_orders = int(arr.poisson(lam))
        if n_orders == 0:
            continue
        # Creation minute within the day by the intraday profile.
        u = arr.random(n_orders)
        hour = np.searchsorted(intraday_cdf, u, side="left")
        minute = arr.integers(0, 60, size=n_orders)
        created = day * _MIN_PER_DAY + hour * 60 + minute
        regions = sample_from_cdf(arr, np.cumsum(region_w), n_orders)
        order_created_min.append(created.astype(np.int64))
        order_rows_region.append(regions.astype(np.int32))

    if not order_created_min:
        return _empty_orders()

    created_all = np.concatenate(order_created_min)
    region_all = np.concatenate(order_rows_region)
    order_sort = np.argsort(created_all, kind="stable")
    created_all = created_all[order_sort]
    region_all = region_all[order_sort]
    n_total = created_all.shape[0]
    order_ids = np.arange(n_total, dtype=np.int64)

    # Number of lines per order.
    n_lines = geometric_counts(basket, cfg.demand.lines_per_order_mean, n_total)
    n_lines = np.clip(n_lines, 1, None)

    # Delivery coordinates: region center + offset within the radius.
    radius = cfg.region_radius_km
    ang = basket.uniform(0, 2 * np.pi, size=n_total)
    rad = radius * np.sqrt(basket.random(n_total))
    dest_x = region_x[region_all] + rad * np.cos(ang)
    dest_y = region_y[region_all] + rad * np.sin(ang)

    total_lines = int(n_lines.sum())
    # Expand into lines.
    line_order = np.repeat(order_ids, n_lines)
    line_region = np.repeat(region_all, n_lines)
    line_created = np.repeat(created_all, n_lines)
    line_dest_x = np.repeat(dest_x, n_lines)
    line_dest_y = np.repeat(dest_y, n_lines)
    # line_id within the order.
    line_id = _within_order_index(n_lines)

    # SKUs by Zipf (rank → sku). TODO v2: adjustment for the region's category.
    sku_rank = sample_from_cdf(basket, pop_cdf, total_lines)
    line_sku = sku_by_rank[sku_rank]
    line_qty = basket.integers(1, cfg.demand.qty_max + 1, size=total_lines).astype(np.int32)

    # Customer cancellation (pre-generated so it is CRN-stable and order-aligned). Drawn only
    # when cancel_rate > 0, so default scenarios see no extra draws and stay byte-identical.
    order_cancel_at = np.full(n_total, -1, dtype=np.int64)
    if cfg.demand.cancel_rate > 0:
        cg = hub.generator("demand_cancel")
        will_cancel = cg.random(n_total) < cfg.demand.cancel_rate
        offset_min = cg.random(n_total) * cfg.demand.cancel_within_hours * 60.0
        order_cancel_at = np.where(
            will_cancel, created_all + offset_min.astype(np.int64), -1
        ).astype(np.int64)
    line_cancel_at = np.repeat(order_cancel_at, n_lines)

    return pd.DataFrame(
        {
            "order_id": line_order,
            "line_id": line_id.astype(np.int32),
            "region_id": line_region.astype(np.int32),
            "created_min": line_created.astype(np.int64),
            "dest_x": line_dest_x.astype(np.float64),
            "dest_y": line_dest_y.astype(np.float64),
            "sku_id": line_sku.astype(np.int64),
            "qty": line_qty,
            "cancel_at_min": line_cancel_at.astype(np.int64),
        }
    )


def _within_order_index(n_lines: np.ndarray) -> np.ndarray:
    """For an array of order lengths, return each line's index within its own order (0,1,2,...)."""
    total = int(n_lines.sum())
    starts = np.zeros(len(n_lines), dtype=np.int64)
    starts[1:] = np.cumsum(n_lines)[:-1]
    # Global index minus the start of its own order = the line's local index.
    return np.arange(total, dtype=np.int64) - np.repeat(starts, n_lines)


def _daily_multipliers(cfg: ScenarioConfig) -> np.ndarray:
    """Yearly seasonality (cosine) + shock peak."""
    days = np.arange(cfg.sim_days)
    season = 1.0 + cfg.demand.seasonality_amplitude * np.cos(2 * np.pi * days / 365.0)
    mult = season.copy()
    sh = cfg.shocks
    if sh.enabled and sh.peak_start_day is not None:
        end = sh.peak_start_day + sh.peak_duration_days
        peak = (days >= sh.peak_start_day) & (days < end)
        mult[peak] *= sh.peak_multiplier
    return mult


def _empty_orders() -> pd.DataFrame:
    cols = {
        "order_id": np.int64,
        "line_id": np.int32,
        "region_id": np.int32,
        "created_min": np.int64,
        "dest_x": np.float64,
        "dest_y": np.float64,
        "sku_id": np.int64,
        "qty": np.int32,
        "cancel_at_min": np.int64,
    }
    return pd.DataFrame({k: np.array([], dtype=v) for k, v in cols.items()})
