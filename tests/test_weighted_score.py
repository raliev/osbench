"""Fast tests for the weighted_score policy and its factors."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from osbench.config.policy import WeightedScoreConfig
from osbench.core.engine import SimEngine
from osbench.core.invariants import validate
from osbench.generate import generate_run, load_world_from_dir
from osbench.metrics.compute import compute_metrics


@pytest.mark.fast
def test_weights_sum_enforced():
    WeightedScoreConfig()  # defaults are valid
    with pytest.raises(Exception):
        WeightedScoreConfig(weights={"distance": 1.0, "promise": 0.5})


@pytest.mark.fast
def test_factor_minmax_degenerate():
    from osbench.policies.sourcing.factors import _minmax

    out = _minmax(np.array([5.0, 5.0, 5.0]), degenerate=0.5)
    assert np.allclose(out, 0.5)
    out2 = _minmax(np.array([0.0, 10.0]), degenerate=0.5)
    assert out2[0] == 0.0 and out2[1] == 1.0


@pytest.mark.fast
def test_weighted_score_runs_and_valid(tmp_path):
    gen = generate_run("small", 0, tmp_path / "gen")
    world = load_world_from_dir(gen)
    orders = pd.read_parquet(gen / "orders.parquet")
    out = tmp_path / "ws"
    SimEngine(world, orders, 0, "weighted_score").run(out)
    assert validate(out)["ok"]
    m = compute_metrics(out, warmup_days=14)
    # A balanced policy: mostly single-parcel and high on-time.
    assert m["split_rate"] < 0.1
    assert m["promise_hit_rate"] > 0.9


@pytest.mark.fast
def test_weighted_score_cheaper_shipping_than_random(tmp_path):
    gen = generate_run("small", 0, tmp_path / "gen")
    world = load_world_from_dir(gen)
    orders = pd.read_parquet(gen / "orders.parquet")
    SimEngine(world, orders, 0, "random").run(tmp_path / "rnd")
    SimEngine(world, orders, 0, "weighted_score").run(tmp_path / "ws")
    m_rnd = compute_metrics(tmp_path / "rnd", warmup_days=14)
    m_ws = compute_metrics(tmp_path / "ws", warmup_days=14)
    assert m_ws["shipping_cost_per_order"] < m_rnd["shipping_cost_per_order"]
