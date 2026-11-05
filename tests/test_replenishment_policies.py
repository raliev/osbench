"""Tests for the replenishment policies: R_Q, forecast_driven, network_allocation (RQ2)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from osbench.config.policy import ReplenishmentPolicyConfig
from osbench.core.engine import SimEngine
from osbench.core.invariants import validate
from osbench.core.network import Network
from osbench.generate import generate_run, load_world_from_dir
from osbench.registry import get_replenishment, list_replenishment


@pytest.mark.fast
def test_all_replenishment_registered():
    assert {"base_stock", "s_S", "R_Q", "forecast_driven", "network_allocation"} <= set(
        list_replenishment()
    )


@pytest.mark.fast
@pytest.mark.parametrize("rep", ["R_Q", "forecast_driven", "network_allocation"])
def test_replenishment_runs_valid(tmp_path, rep):
    gen = generate_run("tiny", 0, tmp_path / "gen")
    world = load_world_from_dir(gen)
    orders = pd.read_parquet(gen / "orders.parquet")
    out = tmp_path / rep
    SimEngine(world, orders, 0, "nearest", rep).run(out)
    assert validate(out)["ok"]


def _policy(name, net, **params):
    return get_replenishment(name)(ReplenishmentPolicyConfig(name=name, params=params), net)


@pytest.mark.fast
def test_R_Q_orders_in_whole_lots(tmp_path):
    net = Network(load_world_from_dir(generate_run("tiny", 0, tmp_path / "g")))
    pol = _policy("R_Q", net)
    s = np.full((net.n_sku, net.n_wh), 10, np.int64)
    S = np.full((net.n_sku, net.n_wh), 30, np.int64)  # Q = S - s = 20
    pos = np.full((net.n_sku, net.n_wh), 5, np.int64)  # below reorder -> order a whole lot
    qty = pol.order_quantities(pos, s, S)
    assert np.all((qty == 0) | (qty % 20 == 0))      # whole multiples of Q
    assert np.all(pos + qty >= s)                    # lifted back above the reorder point


@pytest.mark.fast
def test_forecast_driven_scales_with_demand(tmp_path):
    net = Network(load_world_from_dir(generate_run("tiny", 0, tmp_path / "g")))
    pol = _policy("forecast_driven", net, service_z=1.645)
    s = np.ones((net.n_sku, net.n_wh), np.int64)
    S = np.ones((net.n_sku, net.n_wh), np.int64)
    pos = np.zeros((net.n_sku, net.n_wh), np.int64)
    lo = pol.order_quantities(pos, s, S, demand_rate=np.full_like(pos, 1.0, dtype=float))
    hi = pol.order_quantities(pos, s, S, demand_rate=np.full_like(pos, 5.0, dtype=float))
    assert hi.sum() > lo.sum()  # higher observed demand -> larger order-up-to level


@pytest.mark.fast
def test_network_allocation_follows_demand_share(tmp_path):
    net = Network(load_world_from_dir(generate_run("tiny", 0, tmp_path / "g")))
    pol = _policy("network_allocation", net)
    s = np.ones((net.n_sku, net.n_wh), np.int64)
    S = np.ones((net.n_sku, net.n_wh), np.int64)
    pos = np.zeros((net.n_sku, net.n_wh), np.int64)
    rate = np.zeros((net.n_sku, net.n_wh))
    rate[:, 0] = 4.0  # all demand on warehouse 0
    rate[:, 1:] = 0.0
    qty = pol.order_quantities(pos, s, S, demand_rate=rate)
    # Warehouse 0 (all demand) gets the lion's share vs the rest combined.
    assert qty[:, 0].sum() > qty[:, 1:].sum()
