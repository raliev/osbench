"""Tests for the hindsight lower bound and gap_to_hindsight."""

from __future__ import annotations

import pandas as pd
import pytest

from osbench.core.engine import SimEngine
from osbench.generate import generate_run, load_world_from_dir
from osbench.metrics.compute import compute_metrics, write_metrics
from osbench.metrics.hindsight import gap_to_hindsight, hindsight_cost


@pytest.mark.fast
def test_hindsight_positive(tmp_path):
    gen = generate_run("tiny", 0, tmp_path / "gen")
    h = hindsight_cost(gen, warmup_days=7)
    assert h["hindsight_per_order"] > 0
    assert h["n_orders"] > 0


@pytest.mark.fast
def test_hindsight_is_lower_bound(tmp_path):
    gen = generate_run("tiny", 0, tmp_path / "gen")
    world = load_world_from_dir(gen)
    orders = pd.read_parquet(gen / "orders.parquet")
    out = gen / "nearest"
    SimEngine(world, orders, 0, "nearest").run(out)
    m = compute_metrics(out, warmup_days=7)
    h = hindsight_cost(gen, warmup_days=7)
    # Any policy ships at least as expensively as the offline optimum.
    assert m["shipping_cost_per_order"] >= h["hindsight_per_order"] - 1e-6
    gap = gap_to_hindsight(m["shipping_cost_per_order"], h["hindsight_per_order"])
    assert gap >= -1e-6


@pytest.mark.fast
def test_gap_written_to_metrics(tmp_path):
    gen = generate_run("tiny", 0, tmp_path / "gen")
    world = load_world_from_dir(gen)
    orders = pd.read_parquet(gen / "orders.parquet")
    out = gen / "nearest"
    SimEngine(world, orders, 0, "nearest").run(out)
    m = write_metrics(out, warmup_days=7, scenario_dir=gen)
    assert "gap_to_hindsight" in m and m["gap_to_hindsight"] is not None
    assert m["gap_to_hindsight"] >= -1e-6


@pytest.mark.fast
def test_random_further_from_hindsight_than_nearest(tmp_path):
    gen = generate_run("small", 0, tmp_path / "gen")
    world = load_world_from_dir(gen)
    orders = pd.read_parquet(gen / "orders.parquet")
    gaps = {}
    h = hindsight_cost(gen, warmup_days=14)
    for pol in ("random", "nearest"):
        out = gen / pol
        SimEngine(world, orders, 0, pol).run(out)
        m = compute_metrics(out, warmup_days=14)
        gaps[pol] = gap_to_hindsight(m["shipping_cost_per_order"], h["hindsight_per_order"])
    assert gaps["random"] > gaps["nearest"]
