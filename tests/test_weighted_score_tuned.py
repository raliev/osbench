"""Tests for weighted_score_tuned and the offline weight tuner (RQ1)."""

from __future__ import annotations

import pandas as pd
import pytest

from osbench.config.loader import load_scenario
from osbench.config.policy import SourcingPolicyConfig
from osbench.core.engine import SimEngine
from osbench.core.invariants import validate
from osbench.experiments.tune import evaluate_weights, tune_weighted_score
from osbench.generate import generate_run, load_world_from_dir
from osbench.metrics.compute import compute_metrics
from osbench.registry import list_sourcing

# A deliberately lopsided weight set (almost all weight on distance).
_SKEWED = {"distance": 0.7, "ship_price": 0.1, "promise": 0.1, "splits": 0.04,
           "above_safety": 0.02, "wh_load": 0.02, "wh_rank": 0.02}


@pytest.mark.fast
def test_tuned_registered():
    assert "weighted_score_tuned" in list_sourcing()


@pytest.mark.fast
def test_tuned_runs_valid_on_tiny(tmp_path):
    gen = generate_run("tiny", 0, tmp_path / "gen")
    world = load_world_from_dir(gen)
    orders = pd.read_parquet(gen / "orders.parquet")
    out = tmp_path / "run"
    scfg = SourcingPolicyConfig(name="weighted_score_tuned", params={"weights": _SKEWED})
    SimEngine(world, orders, 0, "weighted_score_tuned", "base_stock", sourcing_cfg=scfg).run(out)
    assert validate(out)["ok"]


@pytest.mark.nightly
def test_tuned_weights_change_outcome(tmp_path):
    # Weights only matter where factors conflict (here: warehouse load vs distance). demo_showcase
    # is capacity-bound, so a load-aware weighting lands differently than the distance-heavy one.
    from osbench.generate import build_orders, build_world
    from osbench.rng.streams import RngHub

    cfg = load_scenario("demo_showcase")

    def run(params):
        hub = RngHub(0)
        world = build_world(cfg, hub)
        orders = build_orders(world, cfg, hub)
        out = tmp_path / ("p" + str(hash(str(params)) & 0xffff))
        scfg = SourcingPolicyConfig(name="weighted_score_tuned", params=params)
        SimEngine(world, orders, 0, "weighted_score_tuned", "base_stock", sourcing_cfg=scfg).run(out)
        return compute_metrics(out, warmup_days=cfg.warmup_days)["total_cost_per_order"]

    assert run({}) != run({"weights": _SKEWED})


@pytest.mark.fast
def test_evaluate_weights_is_deterministic():
    from osbench.config.policy import DEFAULT_WS_WEIGHTS

    a = evaluate_weights("tiny", dict(DEFAULT_WS_WEIGHTS), [0])
    b = evaluate_weights("tiny", dict(DEFAULT_WS_WEIGHTS), [0])
    assert a == b


@pytest.mark.nightly
def test_tuning_never_worse_than_baseline():
    # The baseline weights are evaluated first and kept unless beaten, so the tuned result is
    # never worse than the default on the training seeds.
    res = tune_weighted_score("small", seeds=[0, 1], n_trials=8, tune_seed=0)
    baseline = res["trials"][0]["cost"]
    assert res["best_cost"] <= baseline + 1e-9
    assert abs(sum(res["best_weights"].values()) - 1.0) < 1e-6
