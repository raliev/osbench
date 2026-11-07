"""Sweep runner: scenarios x policies x seeds, parallel by process.

For each (scenario, seed) the world and order stream are generated ONCE and every policy
runs on that identical world (common random numbers), so policies are paired by seed. The
result is a tidy table (one row per scenario/policy/seed) written to ``sweep.parquet``.
"""

from __future__ import annotations

import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

from ..config.schema import ScenarioConfig

# Metrics kept per run in the sweep table.
_METRIC_KEYS = [
    "n_orders", "total_cost_per_order", "shipping_cost_per_order",
    "holding_cost", "purchase_cost", "penalty_cost",
    "promise_hit_rate", "lateness_days_p90", "split_rate", "avg_parcels_per_order",
    "lost_sales_rate", "decision_time_ms_p50", "decision_time_ms_p99",
    "gap_to_hindsight", "hindsight_per_order",
    # Explanatory: where the lost lines and the waiting come from.
    "promise_rejection_rate", "deferred_share", "customer_cancel_rate",
    "shortage_rate_checkout", "shortage_rate_post_order", "shortage_rate_pick",
    "transfer_cost", "transfer_units", "transfer_share",
]


def _run_scenario_seed(task: dict) -> list[dict]:
    """Worker: generate one world, run every policy on it, return metric rows."""
    import pandas as pd

    from ..core.engine import SimEngine
    from ..generate import generate_run, load_world_from_dir
    from ..metrics.compute import write_metrics

    scenario = task["scenario"]
    if isinstance(scenario, dict):
        scenario = ScenarioConfig.model_validate(scenario)
    name = task["name"]
    seed = task["seed"]
    out_dir = Path(task["out_dir"])
    scen_dir = out_dir / f"{name}_s{seed}"
    try:
        generate_run(scenario, seed, scen_dir)
        world = load_world_from_dir(scen_dir)
        orders = pd.read_parquet(scen_dir / "orders.parquet")
        cfg = world.config
        rows = []
        for pol in task["policies"]:
            run_out = scen_dir / pol
            SimEngine(world, orders, seed, pol, task["replenishment"]).run(run_out)
            m = write_metrics(run_out, warmup_days=cfg.warmup_days, scenario_dir=scen_dir)
            if not task["keep_events"]:
                for f in ("events.parquet", "inventory_daily.parquet", "inventory_sku_daily.parquet"):
                    (run_out / f).unlink(missing_ok=True)
            row = {"scenario": name, "policy": pol, "seed": seed,
                   "replenishment": task["replenishment"], **task.get("axes", {})}
            for k in _METRIC_KEYS:
                row[k] = m.get(k)
            rows.append(row)
        return rows
    except Exception as e:  # noqa: BLE001
        return [{"scenario": name, "policy": "__error__", "seed": seed,
                 "error": f"{type(e).__name__}: {e}"}]


def run_sweep(
    scenarios: list,
    policies: list[str],
    seeds: list[int],
    out_dir: str | Path,
    replenishment: str = "base_stock",
    workers: int = 0,
    keep_events: bool = False,
    progress=None,
) -> pd.DataFrame:
    """Run the full sweep and write ``<out_dir>/sweep.parquet``. Returns the results frame.

    ``scenarios`` is a list of (name, ref_or_ScenarioConfig, axes) tuples or plain refs.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    tasks = []
    for item in scenarios:
        if isinstance(item, tuple):
            name, scen, axes = item
        else:
            name, scen, axes = str(item), str(item), {}
        scen_payload = scen.model_dump(mode="json") if isinstance(scen, ScenarioConfig) else scen
        for seed in seeds:
            tasks.append({
                "scenario": scen_payload, "name": name, "seed": seed, "axes": axes,
                "policies": policies, "replenishment": replenishment,
                "out_dir": str(out_dir), "keep_events": keep_events,
            })

    rows: list[dict] = []
    workers = workers or 1
    if workers == 1:
        for i, t in enumerate(tasks):
            rows.extend(_run_scenario_seed(t))
            if progress:
                progress(i + 1, len(tasks))
    else:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for i, res in enumerate(ex.map(_run_scenario_seed, tasks)):
                rows.extend(res)
                if progress:
                    progress(i + 1, len(tasks))

    df = pd.DataFrame(rows)
    df.to_parquet(out_dir / "sweep.parquet", index=False)
    manifest = {"scenarios": [s[0] if isinstance(s, tuple) else str(s) for s in scenarios],
                "policies": policies, "seeds": list(seeds), "replenishment": replenishment,
                "n_tasks": len(tasks)}
    (out_dir / "sweep_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return df
