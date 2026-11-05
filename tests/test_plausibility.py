"""Plausibility (sanity) checks on the small preset. Marked nightly (slower)."""

from __future__ import annotations

import polars as pl
import pytest

from osbench.config.loader import load_scenario
from osbench.config.schema import ShockConfig
from osbench.core.engine import SimEngine
from osbench.generate import build_orders, build_world
from osbench.journal.schema import JType
from osbench.metrics.compute import compute_metrics
from osbench.rng.streams import RngHub


def _run(cfg, seed, out):
    hub = RngHub(seed)
    world = build_world(cfg, hub)
    orders = build_orders(world, cfg, hub)
    SimEngine(world, orders, seed, "nearest").run(out)
    return compute_metrics(out, warmup_days=cfg.warmup_days)


@pytest.mark.nightly
def test_double_demand_raises_cost_per_order(tmp_path):
    base = load_scenario("small")
    dbl = base.model_copy(
        update={"demand": base.demand.model_copy(update={"base_orders_per_day": 400})}
    )
    mb = _run(base, 0, tmp_path / "b")
    md = _run(dbl, 0, tmp_path / "d")
    # More volume against fixed storage/sourcing pushes cost per order up.
    assert md["total_cost_per_order"] > mb["total_cost_per_order"]


@pytest.mark.nightly
def test_high_load_degrades_service(tmp_path):
    base = load_scenario("small")
    whs = tuple(w.model_copy(update={"capacity_lines_per_day": 120}) for w in base.warehouses)
    hi = base.model_copy(
        update={
            "warehouses": whs,
            "demand": base.demand.model_copy(update={"base_orders_per_day": 400}),
        }
    )
    mb = _run(base, 0, tmp_path / "b")
    mh = _run(hi, 0, tmp_path / "h")
    # Capacity-bound network: on-time collapses relative to the baseline.
    assert mh["promise_hit_rate"] < mb["promise_hit_rate"] - 0.1


@pytest.mark.nightly
def test_showcase_weighted_beats_cheapest(tmp_path):
    # Capacity-bound symmetric warehouses + lateness penalty: a load-aware policy should
    # beat the myopic one by a wide margin (it avoids overloading one warehouse).
    cfg = load_scenario("demo_showcase")
    hub = RngHub(0)
    world = build_world(cfg, hub)
    orders = build_orders(world, cfg, hub)
    SimEngine(world, orders, 0, "cheapest_myopic").run(tmp_path / "c")
    SimEngine(world, orders, 0, "weighted_score").run(tmp_path / "w")
    mc = compute_metrics(tmp_path / "c", warmup_days=cfg.warmup_days)
    mw = compute_metrics(tmp_path / "w", warmup_days=cfg.warmup_days)
    # weighted is markedly cheaper and markedly more on-time.
    assert mw["total_cost_per_order"] < 0.8 * mc["total_cost_per_order"]
    assert mw["promise_hit_rate"] > mc["promise_hit_rate"] + 0.3


@pytest.mark.nightly
def test_stranded_weighted_beats_cheapest(tmp_path):
    # Scarcity + ATP tolerance: the central warehouse is the only in-tolerance source for the
    # captive region. A myopic policy drains it on the side regions, stranding stock and losing
    # captive sales; a stock-aware policy preserves it. weighted_score should lose fewer sales
    # and cost less than cheapest_myopic. (This is the ATP-tolerance / stranding mechanism.)
    cfg = load_scenario("demo_stranded")
    hub = RngHub(0)
    world = build_world(cfg, hub)
    orders = build_orders(world, cfg, hub)
    SimEngine(world, orders, 0, "cheapest_myopic").run(tmp_path / "c")
    SimEngine(world, orders, 0, "weighted_score").run(tmp_path / "w")
    mc = compute_metrics(tmp_path / "c", warmup_days=cfg.warmup_days)
    mw = compute_metrics(tmp_path / "w", warmup_days=cfg.warmup_days)
    # weighted strands less stock -> fewer lost sales and a lower total cost per order.
    assert mw["lost_sales_rate"] < mc["lost_sales_rate"]
    assert mw["total_cost_per_order"] < mc["total_cost_per_order"]


@pytest.mark.nightly
def test_warehouse_outage_reroutes(tmp_path):
    base = load_scenario("small")
    out_cfg = base.model_copy(
        update={
            "shocks": ShockConfig(
                enabled=True,
                warehouse_outage_id=0,
                warehouse_outage_days=10,
                warehouse_outage_start_day=20,
            )
        }
    )
    hub = RngHub(0)
    world = build_world(out_cfg, hub)
    orders = build_orders(world, out_cfg, hub)
    out = tmp_path / "outage"
    SimEngine(world, orders, 0, "nearest").run(out)

    ev = pl.read_parquet(out / "events.parquet").filter(pl.col("type") == int(JType.SHIPPED))
    ev = ev.with_columns((pl.col("t") // 1440).alias("day"))

    def shipped(wh, d0, d1):
        return ev.filter(
            (pl.col("warehouse") == wh) & (pl.col("day") >= d0) & (pl.col("day") < d1)
        ).height

    before_wh0 = shipped(0, 10, 20)
    during_wh0 = shipped(0, 20, 30)
    # The downed warehouse ships almost nothing during the outage window.
    assert during_wh0 < 0.1 * max(1, before_wh0)
    # At least one neighbor picks up more volume than before.
    before_neigh = sum(shipped(w, 10, 20) for w in (1, 2, 3))
    during_neigh = sum(shipped(w, 20, 30) for w in (1, 2, 3))
    assert during_neigh > before_neigh
