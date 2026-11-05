"""Offline HTML report for a sweep: paired tables, per-seed scatter, Pareto, sensitivity."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from ..metrics.compare import compare_sweep

_DARK = dict(paper_bgcolor="#161b22", plot_bgcolor="#161b22", font=dict(color="#e6edf3"))
_KNOWN = {
    "scenario", "policy", "seed", "replenishment", "error", "n_orders",
    "total_cost_per_order", "shipping_cost_per_order", "holding_cost", "purchase_cost",
    "penalty_cost", "promise_hit_rate", "lateness_days_p90", "split_rate",
    "avg_parcels_per_order", "lost_sales_rate", "decision_time_ms_p50",
    "decision_time_ms_p99", "gap_to_hindsight", "hindsight_per_order",
}
_PALETTE = ["#2f81f7", "#3fb950", "#d29922", "#f85149", "#a371f7", "#8957e5", "#db61a2"]


def _fig_html(fig: go.Figure, first: bool) -> str:
    return fig.to_html(full_html=False, include_plotlyjs=("inline" if first else False))


def _seed_scatter(sub: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    for i, pol in enumerate(sorted(sub["policy"].unique())):
        p = sub[sub["policy"] == pol].sort_values("seed")
        fig.add_trace(go.Scatter(x=p["seed"], y=p["total_cost_per_order"], mode="lines+markers",
                                 name=pol, line=dict(color=_PALETTE[i % len(_PALETTE)])))
    fig.update_layout(title="Cost/order by seed (CRN: policies move together)",
                      xaxis_title="seed", yaxis_title="cost/order", **_DARK)
    return fig


def _pareto(sub: pd.DataFrame) -> go.Figure:
    g = sub.groupby("policy").agg(
        cost=("total_cost_per_order", "mean"), cost_sd=("total_cost_per_order", "std"),
        ontime=("promise_hit_rate", "mean"), ontime_sd=("promise_hit_rate", "std"),
    ).reset_index()
    fig = go.Figure(go.Scatter(
        x=g["cost"], y=g["ontime"], mode="markers+text", text=g["policy"], textposition="top center",
        error_x=dict(type="data", array=g["cost_sd"].fillna(0)),
        error_y=dict(type="data", array=g["ontime_sd"].fillna(0)),
        marker=dict(size=11, color=[_PALETTE[i % len(_PALETTE)] for i in range(len(g))])))
    fig.update_layout(title="Pareto: cost/order vs on-time (mean ± sd over seeds)",
                      xaxis_title="cost/order", yaxis_title="on-time rate", **_DARK)
    return fig


def _cost_vs_time(sub: pd.DataFrame) -> go.Figure:
    g = sub.groupby("policy").agg(cost=("total_cost_per_order", "mean"),
                                  p99=("decision_time_ms_p99", "mean")).reset_index()
    fig = go.Figure(go.Scatter(x=g["p99"], y=g["cost"], mode="markers+text", text=g["policy"],
                               textposition="top center",
                               marker=dict(size=11, color=[_PALETTE[i % len(_PALETTE)] for i in range(len(g))])))
    fig.update_layout(title="Cost/order vs decision time (p99 ms)",
                      xaxis_title="decision time p99 (ms)", yaxis_title="cost/order", **_DARK)
    return fig


def _paired_table(cmp: pd.DataFrame, scenario: str, baseline: str) -> str:
    sub = cmp[(cmp["scenario"] == scenario) & (cmp["metric"] == "total_cost_per_order")]
    if sub.empty:
        return ""
    rows = ""
    for _, r in sub.iterrows():
        better = r["mean_diff"] < 0
        color = ("#3fb950" if better else "#f85149") if r["significant_95"] else "#8b949e"
        rows += (f"<tr><td>{r['policy']}</td><td>{r['mean_policy']:.3f}</td>"
                 f"<td style='color:{color}'>{r['mean_diff']:+.3f}</td>"
                 f"<td>[{r['t_ci_low']:.3f}, {r['t_ci_high']:.3f}]</td>"
                 f"<td>{'yes' if r['significant_95'] else 'no'}</td></tr>")
    return (f"<table><tr><th>policy</th><th>cost/order</th><th>Δ vs {baseline}</th>"
            f"<th>95% CI</th><th>sig?</th></tr>{rows}</table>")


def _sensitivity(df: pd.DataFrame, axes: list[str]) -> go.Figure | None:
    if len(axes) != 2:
        return None
    a0, a1 = axes
    best = (df.groupby([a0, a1, "policy"])["total_cost_per_order"].mean().reset_index())
    idx = best.groupby([a0, a1])["total_cost_per_order"].idxmin()
    win = best.loc[idx]
    xs = sorted(df[a1].unique())
    ys = sorted(df[a0].unique())
    z = [[float(win[(win[a0] == y) & (win[a1] == x)]["total_cost_per_order"].iloc[0]) for x in xs] for y in ys]
    text = [[str(win[(win[a0] == y) & (win[a1] == x)]["policy"].iloc[0]) for x in xs] for y in ys]
    fig = go.Figure(go.Heatmap(z=z, x=[str(x) for x in xs], y=[str(y) for y in ys],
                               text=text, texttemplate="%{text}", colorscale="YlGnBu_r",
                               colorbar=dict(title="best cost/order")))
    fig.update_layout(title=f"Best policy per cell ({a0} x {a1})",
                      xaxis_title=a1, yaxis_title=a0, **_DARK)
    return fig


def build_sweep_report(sweep_parquet: str | Path, baseline: str, out: str | Path | None = None) -> Path:
    sweep_parquet = Path(sweep_parquet)
    out = Path(out) if out else sweep_parquet.parent / "sweep_report.html"
    df = pd.read_parquet(sweep_parquet)
    df = df[df["policy"] != "__error__"].copy()
    cmp = compare_sweep(df, baseline)
    axes = [c for c in df.columns if c not in _KNOWN]

    parts, first = [], True
    sens = _sensitivity(df, axes)
    if sens is not None:
        parts.append(f'<div class="card">{_fig_html(sens, first)}</div>'); first = False

    for scenario in sorted(df["scenario"].unique()):
        sub = df[df["scenario"] == scenario]
        parts.append(f"<h2>{scenario}</h2>")
        parts.append(f'<div class="card">{_paired_table(cmp, scenario, baseline)}</div>')
        for fig in (_seed_scatter(sub), _pareto(sub), _cost_vs_time(sub)):
            parts.append(f'<div class="card">{_fig_html(fig, first)}</div>'); first = False

    html = _TEMPLATE.format(baseline=baseline, n_policies=df["policy"].nunique(),
                            n_seeds=df["seed"].nunique(), n_scen=df["scenario"].nunique(),
                            body="".join(parts))
    out.write_text(html, encoding="utf-8")
    return out


_TEMPLATE = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>osbench sweep report</title>
<style>body{{background:#0e1117;color:#e6edf3;font:14px system-ui,sans-serif;margin:0;padding:16px}}
h1{{font-size:20px}}h2{{font-size:16px;margin-top:24px;border-top:1px solid #30363d;padding-top:10px}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:8px;margin:10px 0}}
table{{border-collapse:collapse}}td,th{{padding:3px 12px;text-align:left;border-bottom:1px solid #21262d}}
th{{color:#8b949e}}</style></head><body>
<h1>osbench sweep report</h1>
<div style="color:#8b949e">baseline: {baseline} · {n_policies} policies · {n_seeds} seeds · {n_scen} scenarios</div>
{body}</body></html>"""
