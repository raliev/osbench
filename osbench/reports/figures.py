"""Plotly figures built from a run's journal (used by the static report)."""

from __future__ import annotations

from pathlib import Path

import plotly.graph_objects as go
import polars as pl

from ..journal.schema import JType

_MIN_PER_DAY = 1440
_DARK = dict(paper_bgcolor="#161b22", plot_bgcolor="#161b22", font=dict(color="#e6edf3"))


def _load(run_dir: Path) -> pl.DataFrame:
    path = Path(run_dir) / "events.parquet"
    if not path.exists():
        raise FileNotFoundError(
            f"no events.parquet in {run_dir}. Run the simulation first, e.g.:\n"
            f"  osbench run --scenario {Path(run_dir).parent} --sourcing {Path(run_dir).name}"
        )
    return pl.read_parquet(path)


def cost_components_fig(ev: pl.DataFrame) -> go.Figure:
    shipping = float(ev.filter(pl.col("type") == int(JType.SHIPPED))["cost"].sum() or 0)
    holding = float(ev.filter(pl.col("type") == int(JType.HOLDING))["cost"].sum() or 0)
    purchase = float(ev.filter(pl.col("type") == int(JType.RECEIVED))["cost"].sum() or 0)
    penalty = float(ev.filter(pl.col("type") == int(JType.LOST_SALE))["cost"].sum() or 0)
    fig = go.Figure(
        go.Bar(
            x=["shipping", "holding", "purchase", "penalties"],
            y=[shipping, holding, purchase, penalty],
            marker_color=["#2f81f7", "#d29922", "#3fb950", "#f85149"],
        )
    )
    fig.update_layout(title="Cost components (total)", **_DARK)
    return fig


def lateness_fig(ev: pl.DataFrame) -> go.Figure:
    d = ev.filter(pl.col("type") == int(JType.DELIVERED))
    fig = go.Figure()
    if d.height:
        late = ((d["delivered_at"] - d["promised_at"]).cast(pl.Float64) / _MIN_PER_DAY).to_list()
        fig.add_trace(go.Histogram(x=late, marker_color="#2f81f7", nbinsx=40))
    fig.update_layout(
        title="Delivery vs promise (days; <=0 is on time)", xaxis_title="days late", **_DARK
    )
    return fig


def parcels_per_order_fig(ev: pl.DataFrame) -> go.Figure:
    s = ev.filter(pl.col("type") == int(JType.SHIPPED))
    fig = go.Figure()
    if s.height:
        per = s.group_by("order_id").len()["len"].to_list()
        fig.add_trace(go.Histogram(x=per, marker_color="#8957e5"))
    fig.update_layout(title="Parcels per order", xaxis_title="parcels", **_DARK)
    return fig


def shipped_by_warehouse_fig(ev: pl.DataFrame) -> go.Figure:
    s = ev.filter(pl.col("type") == int(JType.SHIPPED)).with_columns(
        (pl.col("t") // _MIN_PER_DAY).alias("day")
    )
    fig = go.Figure()
    if s.height:
        agg = s.group_by(["day", "warehouse"]).len().sort("day")
        for wh in sorted(agg["warehouse"].unique().to_list()):
            w = agg.filter(pl.col("warehouse") == wh).sort("day")
            fig.add_trace(
                go.Scatter(x=w["day"].to_list(), y=w["len"].to_list(), mode="lines", name=f"W{wh}")
            )
    fig.update_layout(title="Shipped parcels per day by warehouse", xaxis_title="day", **_DARK)
    return fig


def all_figures(run_dir: Path) -> list[go.Figure]:
    ev = _load(run_dir)
    return [
        cost_components_fig(ev),
        lateness_fig(ev),
        parcels_per_order_fig(ev),
        shipped_by_warehouse_fig(ev),
    ]
