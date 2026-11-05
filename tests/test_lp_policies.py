"""Tests for the LP dual-price policies (dtlp_bidprice, primal_dual)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from osbench.core.engine import SimEngine
from osbench.core.invariants import validate
from osbench.generate import build_orders, build_world, generate_run, load_world_from_dir
from osbench.metrics.compute import compute_metrics
from osbench.policies.sourcing.lp_common import solve_bid_prices
from osbench.rng.streams import RngHub


@pytest.mark.fast
def test_bid_prices_higher_for_scarce_warehouse():
    # wh0 abundant (supply 5), wh1 scarce (supply 1); demand 4 + 3 across two regions.
    supply = np.array([5.0, 1.0])
    demand = np.array([4.0, 3.0])
    cost = np.array([[1.0, 3.0], [2.0, 1.0]])  # [wh, region]
    bid = solve_bid_prices(supply, demand, cost, unmet_penalty=10.0)
    assert bid.shape == (2,)
    assert bid[1] > bid[0]  # the scarce warehouse carries a higher opportunity cost


@pytest.mark.fast
def test_bid_zero_without_demand_or_supply():
    cost = np.array([[1.0, 2.0], [2.0, 1.0]])
    assert np.allclose(solve_bid_prices(np.zeros(2), np.array([3.0, 3.0]), cost, 10.0), 0.0)
    assert np.allclose(solve_bid_prices(np.array([5.0, 5.0]), np.zeros(2), cost, 10.0), 0.0)


@pytest.mark.fast
@pytest.mark.parametrize("policy", ["dtlp_bidprice", "primal_dual"])
def test_lp_policy_runs_valid(tmp_path, policy):
    gen = generate_run("tiny", 0, tmp_path / "gen")
    world = load_world_from_dir(gen)
    orders = pd.read_parquet(gen / "orders.parquet")
    out = tmp_path / policy
    SimEngine(world, orders, 0, policy).run(out)
    assert validate(out)["ok"]


@pytest.mark.fast
def test_dtlp_not_worse_than_cheapest_at_surplus(tmp_path):
    from osbench.config.loader import load_scenario

    cfg = load_scenario("small").model_copy(update={"initial_cover_days": 30})
    hub = RngHub(0)
    world = build_world(cfg, hub)
    orders = build_orders(world, cfg, hub)
    mc = _run(world, orders, "cheapest_myopic", tmp_path / "c", cfg.warmup_days)
    md = _run(world, orders, "dtlp_bidprice", tmp_path / "d", cfg.warmup_days)
    # At surplus, bid prices are ~0, so DTLP tracks the myopic optimum within a tolerance.
    assert md["total_cost_per_order"] <= mc["total_cost_per_order"] * 1.1
    assert md["split_rate"] < 0.1


def _run(world, orders, policy, out, warmup):
    SimEngine(world, orders, 0, policy).run(out)
    return compute_metrics(out, warmup_days=warmup)
