"""Assemble a single self-contained offline HTML report for one run."""

from __future__ import annotations

import json
from pathlib import Path

from .figures import all_figures

_TEMPLATE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>osbench report — {name}</title>
<style>body{{background:#0e1117;color:#e6edf3;font:14px system-ui,sans-serif;margin:0;padding:16px}}
h1{{font-size:20px}} .meta{{color:#8b949e;margin-bottom:16px}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:8px}}
table{{border-collapse:collapse}} td,th{{padding:2px 10px;text-align:left}}</style>
</head><body>
<h1>osbench report — {name}</h1>
<div class="meta">{meta}</div>
<div class="card"><h3>Key metrics</h3>{metrics_table}</div>
<div class="grid">{figures}</div>
</body></html>"""


def _metrics_table(metrics: dict) -> str:
    keys = [
        "n_orders", "total_cost_per_order", "shipping_cost_per_order", "gap_to_hindsight",
        "promise_hit_rate", "split_rate", "lost_sales_rate", "lateness_days_p90",
        "decision_time_ms_p99",
    ]
    rows = "".join(
        f"<tr><td>{k}</td><td>{metrics.get(k)}</td></tr>" for k in keys if k in metrics
    )
    return f"<table>{rows}</table>"


def build_report(run_dir: Path, out: Path | None = None) -> Path:
    run_dir = Path(run_dir)
    out = Path(out) if out else run_dir / "report.html"

    metrics = {}
    mpath = run_dir / "metrics.json"
    if mpath.exists():
        metrics = json.loads(mpath.read_text(encoding="utf-8"))

    figs = all_figures(run_dir)
    # Inline Plotly once (first figure), the rest reference the loaded library.
    html_parts = []
    for i, fig in enumerate(figs):
        html_parts.append(
            fig.to_html(full_html=False, include_plotlyjs=("inline" if i == 0 else False))
        )
    figures_html = "".join(f'<div class="card">{h}</div>' for h in html_parts)

    manifest = {}
    man_path = run_dir.parent / "manifest.json"
    if man_path.exists():
        manifest = json.loads(man_path.read_text(encoding="utf-8"))
    meta = (
        f"policy: {run_dir.name} · scenario: {manifest.get('scenario', '?')} · "
        f"seed: {manifest.get('seed', '?')} · orders: {metrics.get('n_orders', '?')}"
    )

    html = _TEMPLATE.format(
        name=run_dir.name,
        meta=meta,
        metrics_table=_metrics_table(metrics),
        figures=figures_html,
    )
    out.write_text(html, encoding="utf-8")
    return out
