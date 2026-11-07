"""Tests for stock-sync latency (2.12): the OMS reads a delayed snapshot of WMS book stock."""

from __future__ import annotations

import hashlib

import pandas as pd
import pytest

from osbench.config.loader import load_scenario
from osbench.core.engine import SimEngine
from osbench.core.invariants import validate
from osbench.generate import build_orders, build_world
from osbench.metrics.compute import compute_metrics
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


@pytest.mark.fast
def test_mode_none_is_identical(tmp_path):
    # mode=none must leave the journal byte-identical (bar the wall-clock policy_time_ms column).
    base = load_scenario("demo_scarce")
    m_def = _run(base, 0, tmp_path / "def")
    cfg = base.model_copy(update={"inventory_sync": base.inventory_sync.model_copy(update={"mode": "none"})})
    _run(cfg, 0, tmp_path / "none")
    assert _events_hash(tmp_path / "def") == _events_hash(tmp_path / "none")
    assert m_def["oms_wms_divergence_units"] == 0.0


@pytest.mark.fast
def test_reservation_closes_the_pick_lag_window(tmp_path):
    # With reservation on and accuracy 1.0, lagging *picks* must not change lost sales: the
    # reservation (released only when the debit reaches the snapshot) closes the window.
    base = load_scenario("demo_scarce")
    m0 = _run(base, 0, tmp_path / "none")
    lag = base.inventory_sync.model_copy(update={"mode": "fixed_lag", "lag_minutes": 240,
                                                 "lagged_events": "picks"})
    m1 = _run(base.model_copy(update={"inventory_sync": lag}), 0, tmp_path / "lag")
    assert m1["lost_sales_rate"] == pytest.approx(m0["lost_sales_rate"], abs=1e-9)
    assert validate(tmp_path / "lag")["ok"]


@pytest.mark.fast
def test_lag_without_reservation_creates_stale_snapshot_shortages(tmp_path):
    # Without reservation, lagging picks oversells against a stale snapshot: some pick shortages
    # are attributed to stale_snapshot, and the OMS/WMS divergence is non-zero.
    base = load_scenario("demo_scarce").model_copy(
        update={"reservation": load_scenario("demo_scarce").reservation.model_copy(update={"enabled": False})}
    )
    lag = base.inventory_sync.model_copy(update={"mode": "fixed_lag", "lag_minutes": 240,
                                                 "lagged_events": "picks"})
    m = _run(base.model_copy(update={"inventory_sync": lag}), 0, tmp_path / "lag")
    assert m["shortage_pick_causes"]["stale_snapshot"] > 0
    assert validate(tmp_path / "lag")["ok"]


@pytest.mark.fast
def test_periodic_mode_runs_and_validates(tmp_path):
    base = load_scenario("demo_scarce")
    sync = base.inventory_sync.model_copy(update={"mode": "periodic", "period_minutes": 60})
    m = _run(base.model_copy(update={"inventory_sync": sync}), 0, tmp_path / "per")
    assert validate(tmp_path / "per")["ok"]
    assert m["oms_wms_divergence_units"] >= 0.0
