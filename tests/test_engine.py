"""Core tests: run, invariants, determinism, baseline policies."""

from __future__ import annotations

import pandas as pd
import polars as pl
import pytest

from osbench.core.engine import SimEngine
from osbench.core.invariants import validate
from osbench.generate import generate_run, load_world_from_dir
from osbench.metrics.compute import compute_metrics


def _run(tmp_path, preset, sourcing, seed=0):
    gen = generate_run(preset, seed, tmp_path / "gen")
    world = load_world_from_dir(gen)
    orders = pd.read_parquet(gen / "orders.parquet")
    out = tmp_path / sourcing
    SimEngine(world, orders, seed, sourcing).run(out)
    return out


@pytest.mark.fast
@pytest.mark.parametrize("sourcing", ["random", "nearest", "cheapest_myopic"])
def test_run_invariants_tiny(tmp_path, sourcing):
    out = _run(tmp_path, "tiny", sourcing)
    res = validate(out)
    assert res["ok"], res["violations"]


@pytest.mark.fast
def test_determinism_journal(tmp_path):
    a = _run(tmp_path / "a", "tiny", "nearest")
    b = _run(tmp_path / "b", "tiny", "nearest")
    # The journal without the wall-clock field must match bit for bit.
    ea = pl.read_parquet(a / "events.parquet").drop("policy_time_ms")
    eb = pl.read_parquet(b / "events.parquet").drop("policy_time_ms")
    assert ea.equals(eb)


@pytest.mark.fast
def test_nearest_cheaper_shipping_than_random(tmp_path):
    rnd = _run(tmp_path, "small", "random")
    near = _run(tmp_path, "small", "nearest")
    m_rnd = compute_metrics(rnd, warmup_days=14)
    m_near = compute_metrics(near, warmup_days=14)
    # Plausibility criterion: nearest is at least 20% cheaper on shipping.
    assert m_near["shipping_cost_per_order"] <= 0.8 * m_rnd["shipping_cost_per_order"]
    # And noticeably fewer splits.
    assert m_near["split_rate"] < m_rnd["split_rate"]


@pytest.mark.fast
def test_cost_components_sum(tmp_path):
    out = _run(tmp_path, "tiny", "nearest")
    m = compute_metrics(out, warmup_days=0)
    total = (m["shipping_cost"] + m["holding_cost"] + m["purchase_cost"]
             + m["penalty_cost"] + m["lateness_penalty_cost"])
    assert abs(total - m["total_cost"]) < 1e-6


def _run_cfg(tmp_path, preset, **updates):
    """Run nearest on a preset with top-level config sections overridden (e.g. reservation)."""
    gen = generate_run(preset, 0, tmp_path / "gen")
    world = load_world_from_dir(gen)
    world.config = world.config.model_copy(update={
        k: getattr(world.config, k).model_copy(update=v) for k, v in updates.items()})
    orders = pd.read_parquet(gen / "orders.parquet")
    eng = SimEngine(world, orders, 0, "nearest")
    eng.run(tmp_path / "out")
    return eng, compute_metrics(tmp_path / "out", warmup_days=world.config.warmup_days)


@pytest.mark.fast
def test_reservation_off_holds_nothing(tmp_path):
    # reservation.enabled=False: nothing is ever reserved, stock only leaves at pick.
    on, _ = _run_cfg(tmp_path / "on", "demo_scarce", reservation={"enabled": True},
                     atp={"method": "on_hand_only"})
    off, m_off = _run_cfg(tmp_path / "off", "demo_scarce", reservation={"enabled": False},
                          atp={"method": "on_hand_only"})
    assert (off.inv.reserved == 0).all()
    # Orders now race for the same units: the conflict surfaces as pick shortages.
    assert off.st_short_pick > on.st_short_pick
    assert m_off["lines_cancelled_by_stage"]["pick"] > 0


@pytest.mark.fast
def test_atp_on_hand_only_ignores_safety_stock(tmp_path):
    eng, m_basic = _run_cfg(tmp_path / "b", "demo_scarce", atp={"method": "basic"})
    basic = eng._atp()
    eng._atp_method = "on_hand_only"  # same state, other formula: safety stock is not held back
    onhand = eng._atp()
    assert (onhand >= basic).all() and (onhand > basic).any()
    _, m_onhand = _run_cfg(tmp_path / "o", "demo_scarce", atp={"method": "on_hand_only"})
    assert m_onhand["lost_sales_rate"] != m_basic["lost_sales_rate"]
