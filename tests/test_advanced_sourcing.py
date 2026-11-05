"""Tests for hindsight_lp, spillover_limit, rolling_milp sourcing policies."""

from __future__ import annotations

import pandas as pd
import pytest

from osbench.config.loader import load_scenario
from osbench.core.engine import SimEngine
from osbench.core.invariants import validate
from osbench.generate import build_orders, build_world, generate_run, load_world_from_dir
from osbench.metrics.compute import compute_metrics
from osbench.registry import list_sourcing
from osbench.rng.streams import RngHub


@pytest.mark.fast
def test_advanced_policies_registered():
    assert {"hindsight_lp", "spillover_limit", "rolling_milp"} <= set(list_sourcing())


@pytest.mark.fast
@pytest.mark.parametrize("pol", ["hindsight_lp", "spillover_limit", "rolling_milp"])
def test_advanced_policy_runs_valid(tmp_path, pol):
    gen = generate_run("small", 0, tmp_path / "gen")
    world = load_world_from_dir(gen)
    orders = pd.read_parquet(gen / "orders.parquet")
    out = tmp_path / pol
    SimEngine(world, orders, 0, pol, "base_stock").run(out)
    assert validate(out)["ok"]


@pytest.mark.fast
def test_spillover_limit_caps_foreign_load(tmp_path):
    # A tight spillover cap must not starve orders: it still fulfils and stays valid.
    gen = generate_run("small", 0, tmp_path / "gen")
    world = load_world_from_dir(gen)
    orders = pd.read_parquet(gen / "orders.parquet")
    from osbench.config.policy import SourcingPolicyConfig
    scfg = SourcingPolicyConfig(name="spillover_limit", params={"spillover_frac": 0.1})
    out = tmp_path / "spill"
    SimEngine(world, orders, 0, "spillover_limit", "base_stock", sourcing_cfg=scfg).run(out)
    assert validate(out)["ok"]


@pytest.mark.nightly
@pytest.mark.parametrize("pol", ["spillover_limit", "hindsight_lp", "rolling_milp"])
def test_capacity_aware_beats_cheapest_on_showcase(tmp_path, pol):
    # On the capacity-bound showcase, load/flow-aware policies beat the myopic one.
    cfg = load_scenario("demo_showcase")

    def run(p):
        hub = RngHub(0)
        world = build_world(cfg, hub)
        orders = build_orders(world, cfg, hub)
        out = tmp_path / p
        SimEngine(world, orders, 0, p, "base_stock").run(out)
        return compute_metrics(out, warmup_days=cfg.warmup_days)["total_cost_per_order"]

    assert run(pol) < run("cheapest_myopic")
