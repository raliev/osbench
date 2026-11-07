"""Tests for inter-warehouse transfers (2.13): lanes, policies, guards, conservation."""

from __future__ import annotations

import hashlib

import pandas as pd
import pytest

from osbench.config.loader import load_scenario
from osbench.core.engine import SimEngine
from osbench.core.invariants import validate
from osbench.generate import build_orders, build_world
from osbench.metrics.compute import compute_metrics
from osbench.registry import get_transfer, list_transfer
from osbench.rng.streams import RngHub


def _run(cfg, seed, out, pol="weighted_score"):
    hub = RngHub(seed)
    world = build_world(cfg, hub)
    orders = build_orders(world, cfg, hub)
    SimEngine(world, orders, seed, pol, "base_stock").run(out)
    return compute_metrics(out, warmup_days=cfg.warmup_days)


def _events_hash(run_dir) -> str:
    ev = pd.read_parquet(run_dir / "events.parquet").drop(columns=["policy_time_ms"])
    return hashlib.md5(pd.util.hash_pandas_object(ev, index=False).values.tobytes()).hexdigest()


def _enable(base, policy="threshold_balance", **params):
    tc = base.transfers.model_copy(update={
        "enabled": True, "policy": policy,
        "params": {"low_days": 5.0, "high_days": 15.0, "target_days": 10.0, **params},
    })
    return base.model_copy(update={"transfers": tc})


@pytest.mark.fast
def test_registry_has_builtin_transfer_policies():
    # Importing the package (done lazily by the engine) registers the three built-ins.
    import osbench.policies.transfer  # noqa: F401
    assert set(list_transfer()) >= {"none", "threshold_balance", "pull_base_stock"}
    assert get_transfer("threshold_balance").name == "threshold_balance"


@pytest.mark.fast
def test_disabled_is_identical(tmp_path):
    base = load_scenario("demo_scarce")
    _run(base, 0, tmp_path / "off")
    cfg = base.model_copy(update={"transfers": base.transfers.model_copy(update={"enabled": False})})
    _run(cfg, 0, tmp_path / "off2")
    assert _events_hash(tmp_path / "off") == _events_hash(tmp_path / "off2")


@pytest.mark.fast
def test_threshold_balance_moves_stock_and_conserves(tmp_path):
    base = load_scenario("demo_scarce")
    m = _run(_enable(base), 0, tmp_path / "thr")
    assert m["transfer_units"] > 0
    assert m["transfer_cost"] == pytest.approx(0.0)  # default lanes are free
    assert validate(tmp_path / "thr")["ok"]  # stock conserved, no negative balances, no stuck orders


@pytest.mark.fast
def test_threshold_gate_controls_volume(tmp_path):
    # The low_days gate drives the mechanism: at low_days=0 no receiver is ever "below cover",
    # so nothing moves; a generous low_days triggers pulls.
    base = load_scenario("demo_two_cities").model_copy(update={"initial_cover_days": 10})
    off = _run(_enable(base, low_days=0.0, high_days=0.0, target_days=0.0), 0, tmp_path / "off")
    on = _run(_enable(base, low_days=8.0, high_days=12.0, target_days=10.0), 0, tmp_path / "on")
    assert off["transfer_units"] == 0
    assert on["transfer_units"] > 0


@pytest.mark.fast
def test_guards_stop_ping_pong(tmp_path):
    base = load_scenario("demo_two_cities").model_copy(update={"initial_cover_days": 8})
    # low == high removes the hysteresis; with guards off the stock ping-pongs.
    no_guard = base.transfers.model_copy(update={
        "enabled": True, "policy": "threshold_balance",
        "params": {"low_days": 10.0, "high_days": 10.0, "target_days": 10.0},
        "guards": base.transfers.guards.model_copy(update={"cooldown_days": 0, "max_source_share": 1.0}),
    })
    m_ng = _run(base.model_copy(update={"transfers": no_guard}), 0, tmp_path / "ng")
    # With the cooldown guard the reverse move is blocked, so ping-pong is zero.
    guarded = no_guard.model_copy(update={
        "guards": base.transfers.guards.model_copy(update={"cooldown_days": 7})})
    m_g = _run(base.model_copy(update={"transfers": guarded}), 0, tmp_path / "g")
    assert m_ng["ping_pong_units"] > 0
    assert m_g["ping_pong_units"] == 0


@pytest.mark.fast
def test_pull_base_stock_runs(tmp_path):
    base = load_scenario("demo_scarce")
    m = _run(_enable(base, policy="pull_base_stock", S=10.0), 0, tmp_path / "pull")
    assert m["transfer_units"] > 0
    assert validate(tmp_path / "pull")["ok"]
