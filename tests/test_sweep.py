"""Tests for the sweep runner, grid expansion, and paired-comparison tables."""

from __future__ import annotations

import pandas as pd
import pytest

from osbench.experiments.grid import expand_grid
from osbench.experiments.sweep import run_sweep
from osbench.metrics.compare import compare_sweep
from osbench.reports.sweep_report import build_sweep_report


@pytest.mark.fast
def test_expand_grid():
    cells = expand_grid("tiny", {"initial_cover_days": [7, 30], "demand.base_orders_per_day": [20, 40]})
    assert len(cells) == 4
    names = {c[0] for c in cells}
    assert any("initial_cover_days7" in n and "base_orders_per_day40" in n for n in names)
    # Axis values applied to the config.
    for name, cfg, axes in cells:
        assert cfg.initial_cover_days == axes["initial_cover_days"]
        assert cfg.demand.base_orders_per_day == axes["demand.base_orders_per_day"]


@pytest.mark.fast
def test_run_sweep_and_compare(tmp_path):
    scenarios = [("tiny", "tiny", {})]
    df = run_sweep(scenarios, ["nearest", "cheapest_myopic"], [0, 1, 2], tmp_path / "s",
                   workers=1, keep_events=False)
    assert (tmp_path / "s" / "sweep.parquet").exists()
    # One row per scenario x policy x seed.
    assert len(df) == 1 * 2 * 3
    assert set(df["policy"]) == {"nearest", "cheapest_myopic"}
    assert (df["policy"] != "__error__").all()
    assert df["total_cost_per_order"].notna().all()

    cmp = compare_sweep(df, "nearest")
    assert not cmp.empty
    row = cmp[(cmp["policy"] == "cheapest_myopic") & (cmp["metric"] == "total_cost_per_order")].iloc[0]
    assert row["n_seeds"] == 3
    assert "t_ci_low" in row and "significant_95" in row


@pytest.mark.fast
def test_sweep_report_offline(tmp_path):
    scenarios = list(expand_grid("tiny", {"initial_cover_days": [7, 30],
                                          "demand.base_orders_per_day": [20, 40]}))
    run_sweep(scenarios, ["nearest", "cheapest_myopic"], [0, 1], tmp_path / "s", workers=1)
    report = build_sweep_report(tmp_path / "s" / "sweep.parquet", "nearest")
    html = report.read_text(encoding="utf-8")
    import re

    assert not re.findall(r'<script[^>]*src=["\']https?://', html)   # offline
    assert "Best policy per cell" in html                            # 2-axis heatmap
    assert "sweep report" in html.lower()
