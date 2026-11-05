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
