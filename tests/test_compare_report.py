"""Tests for paired comparison statistics, compare.csv, and the static report."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from osbench.core.engine import SimEngine
from osbench.generate import generate_run, load_world_from_dir
from osbench.metrics.compare import compare_runs
from osbench.metrics.compute import write_metrics
from osbench.reports.builder import build_report
from osbench.reports.stats import paired_ci, pareto_front


@pytest.mark.fast
def test_paired_ci_basic():
    # Constant positive difference across seeds -> significant, zero-width CI.
    r = paired_ci(np.array([2.0, 2.0, 2.0, 2.0]))
    assert r["mean_diff"] == 2.0
    assert r["significant_95"] is True
    # Symmetric around zero -> not significant.
    r2 = paired_ci(np.array([-1.0, 1.0, -1.0, 1.0]))
    assert r2["significant_95"] is False


@pytest.mark.fast
def test_pareto_front():
    pts = np.array([[1.0, 1.0], [2.0, 2.0], [1.0, 3.0]])  # minimization both axes
    front = set(pareto_front(pts).tolist())
    assert 0 in front and 1 not in front  # (1,1) dominates (2,2)


@pytest.mark.fast
def test_compare_runs_table(tmp_path):
    dirs = []
    for seed in (0, 1):
        gen = generate_run("tiny", seed, tmp_path / f"s{seed}")
        world = load_world_from_dir(gen)
        orders = pd.read_parquet(gen / "orders.parquet")
        for pol in ("nearest", "random"):
            out = gen / pol
            SimEngine(world, orders, seed, pol).run(out)
            write_metrics(out, warmup_days=7)
            dirs.append(out)
    df = compare_runs(dirs, baseline_policy="nearest")
    assert not df.empty
    assert set(df["policy"]) == {"random"}
    row = df[df["metric"] == "shipping_cost_per_order"].iloc[0]
    assert row["n_seeds"] == 2
    # random ships more expensively than nearest -> positive diff.
    assert row["mean_diff"] > 0


@pytest.mark.fast
def test_build_report_offline(tmp_path):
    gen = generate_run("tiny", 0, tmp_path / "gen")
    world = load_world_from_dir(gen)
    orders = pd.read_parquet(gen / "orders.parquet")
    out = gen / "nearest"
    SimEngine(world, orders, 0, "nearest").run(out)
    write_metrics(out, warmup_days=7)
    report = build_report(out)
    html = report.read_text(encoding="utf-8")
    assert report.exists()
    # Offline: no external script sources.
    import re

    assert not re.findall(r'<script[^>]*src=["\']https?://', html)
    assert "total_cost_per_order" in html
