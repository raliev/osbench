"""Tests for the three shortage points (2.9): checkout, post-order, and pick."""

from __future__ import annotations

import pytest

from osbench.config.loader import load_scenario
from osbench.core.engine import SimEngine
from osbench.core.invariants import validate
from osbench.generate import build_orders, build_world
from osbench.metrics.compute import compute_metrics
from osbench.rng.streams import RngHub


def _run(cfg, seed, out):
    hub = RngHub(seed)
    world = build_world(cfg, hub)
    orders = build_orders(world, cfg, hub)
    SimEngine(world, orders, seed, "nearest", "base_stock").run(out)
    return compute_metrics(out, warmup_days=cfg.warmup_days)


@pytest.mark.fast
def test_uncovered_lines_cancel_at_post_order(tmp_path):
    # Under scarcity the default path cancels unsourceable lines at the post-order stage.
    base = load_scenario("demo_scarce")
    m = _run(base, 0, tmp_path / "base")
    stage = m["lines_cancelled_by_stage"]
    assert stage["post_order"] > 0 and stage["checkout"] == 0 and stage["pick"] == 0
    assert validate(tmp_path / "base")["ok"]


@pytest.mark.fast
def test_checkout_atp_check_shifts_cancellations_earlier(tmp_path):
    # With the checkout ATP check on, shortages are caught at checkout instead of post-order.
    base = load_scenario("demo_scarce")
    cfg = base.model_copy(update={"shortage": base.shortage.model_copy(
        update={"checkout_atp_check": True, "p_partial": 0.7})})
    m = _run(cfg, 0, tmp_path / "chk")
    stage = m["lines_cancelled_by_stage"]
    assert stage["checkout"] > stage["post_order"]  # most shortages now caught at checkout
    assert validate(tmp_path / "chk")["ok"]


@pytest.mark.fast
def test_inventory_accuracy_creates_pick_shortages(tmp_path):
    # Inaccurate receipts create phantom inventory discovered at pick -> pick-stage cancellations;
    # invariants must still hold (book reconciled to physical, no negative stock).
    base = load_scenario("demo_scarce")
    cfg = base.model_copy(update={"inventory_accuracy": base.inventory_accuracy.model_copy(
        update={"accuracy": 0.9, "error_mean": 3.0})})
    m = _run(cfg, 0, tmp_path / "inv")
    assert m["lines_cancelled_by_stage"]["pick"] > 0
    assert validate(tmp_path / "inv")["ok"]


@pytest.mark.fast
def test_accurate_inventory_has_no_pick_shortage(tmp_path):
    # With perfect accuracy (default) and reservation on, pick never finds a shortfall.
    base = load_scenario("demo_scarce")
    m = _run(base, 0, tmp_path / "acc")
    assert m["lines_cancelled_by_stage"]["pick"] == 0


@pytest.mark.fast
def test_cancel_whole_order_branch(tmp_path):
    # p_partial_ship < 1 enables cancelling the whole order (not just the short lines) and stays valid.
    base = load_scenario("demo_scarce")
    cfg = base.model_copy(update={"shortage": base.shortage.model_copy(update={"p_partial_ship": 0.0})})
    _run(cfg, 0, tmp_path / "whole")
    assert validate(tmp_path / "whole")["ok"]


@pytest.mark.fast
def test_substitution_at_checkout(tmp_path):
    base = load_scenario("demo_scarce")
    cfg = base.model_copy(update={"shortage": base.shortage.model_copy(
        update={"checkout_atp_check": True, "p_substitute": 0.8})})
    m = _run(cfg, 0, tmp_path / "sub")
    assert m["substituted_lines"] > 0
    assert validate(tmp_path / "sub")["ok"]


@pytest.mark.nightly
def test_accept_slow_rescues_out_of_tolerance(tmp_path):
    # With ATP tolerance on, p_accept_slow places out-of-tolerance lines instead of losing them.
    base = load_scenario("demo_stranded")  # tolerance enabled
    m0 = _run(base, 0, tmp_path / "no")
    cfg = base.model_copy(update={"shortage": base.shortage.model_copy(update={"p_accept_slow": 1.0})})
    m1 = _run(cfg, 0, tmp_path / "slow")
    assert m1["lost_sales_rate"] < m0["lost_sales_rate"]
    assert validate(tmp_path / "slow")["ok"]


@pytest.mark.fast
@pytest.mark.parametrize("opt", ["p_resource", "backorder"])
def test_pick_resolution_strategies_valid(tmp_path, opt):
    # Re-sourcing / backorder at pick (with phantom inventory) run and keep invariants.
    base = load_scenario("demo_scarce")
    sh = {"p_resource": 0.9} if opt == "p_resource" else {"backorder": True}
    cfg = base.model_copy(update={
        "shortage": base.shortage.model_copy(update=sh),
        "inventory_accuracy": base.inventory_accuracy.model_copy(
            update={"accuracy": 0.85, "error_mean": 4.0})})
    _run(cfg, 0, tmp_path / opt)
    assert validate(tmp_path / opt)["ok"]


@pytest.mark.nightly
def test_overloaded_run_finalizes_in_flight_lines(tmp_path):
    # A capacity-bound network leaves orders in the queue at the horizon; they must be closed
    # (unfulfilled-at-horizon) so every line is terminal and validate passes.
    base = load_scenario("demo_showcase")
    m = _run(base, 0, tmp_path / "ov")
    assert m["lines_cancelled_by_stage"]["horizon"] > 0
    assert validate(tmp_path / "ov")["ok"]


@pytest.mark.fast
def test_cumulative_atp_and_process_times_valid(tmp_path):
    base = load_scenario("tiny")
    cfg = base.model_copy(update={
        "atp": base.atp.model_copy(update={"method": "cumulative"}),
        "promise": base.promise.model_copy(update={"method": "atp_based"}),
        "process_times": base.process_times.model_copy(
            update={"pick_median_min": 20.0, "pack_median_min": 8.0})})
    _run(cfg, 0, tmp_path / "cum")
    assert validate(tmp_path / "cum")["ok"]
