"""Tests for promise methods (atp_based / percentile / acceptance) and deferred sourcing (RQ5)."""

from __future__ import annotations

import polars as pl
import pytest

from osbench.config.loader import load_scenario
from osbench.core.engine import SimEngine
from osbench.core.invariants import validate
from osbench.core.promise import accept_probability, compute_promise_min, noise_quantile_days
from osbench.generate import build_orders, build_world
from osbench.journal.schema import JType
from osbench.metrics.compute import compute_metrics
from osbench.rng.streams import RngHub

_DAY = 1440


def _run(cfg, seed, tmp):
    hub = RngHub(seed)
    world = build_world(cfg, hub)
    orders = build_orders(world, cfg, hub)
    SimEngine(world, orders, seed, "nearest", "base_stock").run(tmp)
    return compute_metrics(tmp, warmup_days=cfg.warmup_days), pl.read_parquet(tmp / "events.parquet")


# ------------------------------------------------------------------ promise unit
@pytest.mark.fast
def test_promise_methods_differ():
    from osbench.config.schema import PromiseConfig

    base = PromiseConfig(method="zone_fixed", min_processing_days=1, buffer_days=2, cap_days=20)
    transit = 3
    zf = compute_promise_min(0, transit, base)
    tb = compute_promise_min(0, transit, base.model_copy(update={"method": "transit_plus_buffer"}))
    atp = compute_promise_min(0, transit, base.model_copy(update={"method": "atp_based"}),
                              avail_delay_days=5)
    pct = compute_promise_min(0, transit, base.model_copy(update={"method": "percentile"}),
                              noise_quantile_days=2)
    assert tb > zf  # buffer pushes the promise out
    assert atp > zf  # waiting for stock pushes it out further
    assert pct > zf  # a delivery-noise quantile adds days
    # cap_days clamps the promise.
    capped = compute_promise_min(0, transit, base.model_copy(update={"method": "atp_based",
                                 "cap_days": 2}), avail_delay_days=50)
    assert capped // _DAY == 2  # promised day clamped to cap_days (end-of-day minute)


@pytest.mark.fast
def test_accept_probability_and_noise_quantile():
    from osbench.config.schema import PromiseConfig

    cfg = PromiseConfig(cap_days=10, p_accept_promise=0.0)
    assert accept_probability(3, 3, cfg) == 1.0      # at the baseline: always accept
    assert accept_probability(10, 3, cfg) == 0.0     # at cap with floor 0: never accept
    assert 0.0 < accept_probability(6, 3, cfg) < 1.0  # linear in between
    # Default floor 1.0 => always accept, whatever the length.
    assert accept_probability(10, 3, cfg.model_copy(update={"p_accept_promise": 1.0})) == 1.0
    assert noise_quantile_days(0.0, 0.9) == 0        # no noise -> no added days
    assert noise_quantile_days(2.0, 0.9) >= 1        # some noise -> a positive quantile


# ------------------------------------------------------------------ engine behaviour
@pytest.mark.fast
def test_promise_method_runs_valid(tmp_path):
    base = load_scenario("tiny")
    for method in ("zone_fixed", "transit_plus_buffer", "atp_based", "percentile"):
        cfg = base.model_copy(update={"promise": base.promise.model_copy(update={"method": method})})
        out = tmp_path / method
        _run(cfg, 0, out)
        assert validate(out)["ok"]


@pytest.mark.fast
def test_promise_rejection_logs_and_loses(tmp_path):
    base = load_scenario("demo_scarce")
    cfg = base.model_copy(update={"promise": base.promise.model_copy(
        update={"method": "atp_based", "p_accept_promise": 0.2, "cap_days": 12})})
    m, ev = _run(cfg, 0, tmp_path / "rej")
    rejected = ev.filter(pl.col("type") == int(JType.PROMISE_REJECTED)).height
    assert rejected > 0            # long promises are refused at checkout
    assert validate(tmp_path / "rej")["ok"]


@pytest.mark.nightly
def test_deferral_reduces_lost_sales(tmp_path):
    # Under scarcity with replenishment arriving, waiting for a receipt rescues orders that the
    # myopic path would cancel -> deferred sourcing lowers lost sales (at some cost in slack).
    base = load_scenario("demo_scarce")
    mn, evn = _run(base, 0, tmp_path / "none")
    cfg = base.model_copy(update={"deferral": base.deferral.model_copy(
        update={"mode": "wait_for_receipt"})})
    md, evd = _run(cfg, 0, tmp_path / "defer")
    deferred = evd.filter(pl.col("type") == int(JType.DEFERRED)).height
    assert deferred > 0                                   # orders were actually parked
    assert md["lost_sales_rate"] < mn["lost_sales_rate"]  # and fewer sales are lost
    assert validate(tmp_path / "defer")["ok"]             # invariants hold (no stuck reserves)


@pytest.mark.fast
def test_deferral_default_off_is_unchanged(tmp_path):
    # mode=none (default) must not change behaviour vs the baseline run.
    base = load_scenario("demo_scarce")
    m0, ev0 = _run(base, 1, tmp_path / "a")
    deferred = ev0.filter(pl.col("type") == int(JType.DEFERRED)).height
    assert deferred == 0
