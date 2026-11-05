"""osbench CLI (Typer). v1 stage 1: the generate command."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console

from ..config.loader import load_scenario
from ..generate import generate_run
from ..registry import list_replenishment, list_sourcing

app = typer.Typer(add_completion=False, help="Order-sourcing benchmark (osbench).")
console = Console()

def _resolve_scenario(scenario: str, seed: int) -> Path:
    """Return a run_dir with generated data, generating it on the fly if needed.

    ``scenario`` may be: an existing run_dir (used as is), a preset name, or a YAML path
    (auto-generated into runs/<name>_s<seed>/).
    """
    from ..config.loader import available_presets

    path = Path(scenario)
    if (path / "orders.parquet").exists():
        return path
    # Not a ready run_dir: treat as a config reference (preset name or YAML path).
    is_config = scenario in available_presets() or path.suffix in {".yaml", ".yml"}
    if not is_config:
        raise typer.BadParameter(
            f"{scenario!r} is neither a generated run_dir nor a preset/YAML config"
        )
    cfg = load_scenario(scenario)
    out = Path("runs") / f"{cfg.name}_s{seed}"
    console.print(f"[yellow]Scenario not generated — generating[/yellow] {out} ...")
    generate_run(cfg, seed, out)
    return out


@app.command()
def generate(
    config: str = typer.Option(..., "--config", "-c", help="Preset (tiny/small/...) or path to a YAML file"),
    seed: int = typer.Option(0, "--seed", "-s"),
    out: Path = typer.Option(..., "--out", "-o", help="run_dir directory to write the world and orders into"),
    json_out: bool = typer.Option(False, "--json", help="JSON summary to stdout"),
) -> None:
    """Generate the world and a pre-generated stream of orders."""
    cfg = load_scenario(config)
    run_dir = generate_run(cfg, seed, out)
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    if json_out:
        typer.echo(json.dumps({"run_dir": str(run_dir), **manifest}))
    else:
        console.print(f"[green]✓[/green] Generated: {run_dir}")
        console.print(f"  scenario: {cfg.name}, seed: {seed}")
        console.print(f"  warehouses: {cfg.n_warehouses}, SKU: {cfg.sku.n_sku}, regions: {cfg.n_regions}")
        console.print(f"  order lines: {manifest.get('n_orders_lines')}")
        console.print(f"  config_hash: {manifest['config_hash'][:12]}")


@app.command()
def run(
    scenario: str = typer.Option(
        ...,
        "--scenario",
        help="run_dir from generate, OR a preset name / YAML path (auto-generated if missing)",
    ),
    sourcing: str = typer.Option("nearest", "--sourcing"),
    replenishment: str = typer.Option("base_stock", "--replenishment"),
    out: Path = typer.Option(None, "--out", help="result directory (default <scenario>/<sourcing>)"),
    seed: int = typer.Option(0, "--seed", "-s"),
    live: bool = typer.Option(False, "--live", help="live monitoring"),
    serve: bool = typer.Option(False, "--serve", help="start the web dashboard"),
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8750, "--port"),
    open_browser: bool = typer.Option(True, "--open/--no-open"),
    snapshot_every_hours: int = typer.Option(6, "--snapshot-every-hours"),
    guardrails: bool = typer.Option(False, "--guardrails", help="auto-stop on thresholds"),
    inventory_detail: bool = typer.Option(
        None, "--inventory-detail/--no-inventory-detail",
        help="write per-SKU daily stock for the inspector (auto for small scenarios)",
    ),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Run the simulation on a generated scenario with the chosen policies."""
    import pandas as pd

    from ..core.engine import SimEngine
    from ..core.invariants import validate
    from ..generate import load_world_from_dir

    from ..metrics.compute import write_metrics

    scenario_dir = _resolve_scenario(scenario, seed)
    world = load_world_from_dir(scenario_dir)
    orders = pd.read_parquet(scenario_dir / "orders.parquet")
    out_dir = out or (scenario_dir / sourcing)

    hub = None
    server = None
    if live or serve:
        from ..live.control import ControlFlags
        from ..live.hub import LiveHub

        meta = {
            "preset": world.config.name,
            "policy": sourcing,
            "wh_names": world.warehouses["name"].tolist(),
            "wh_x": world.warehouses["x"].tolist(),
            "wh_y": world.warehouses["y"].tolist(),
            "region_x": world.regions["x"].tolist(),
            "region_y": world.regions["y"].tolist(),
        }
        hub = LiveHub(meta, ControlFlags())
        if serve:
            from ..live.server import LiveServer

            server = LiveServer(hub, host=host, port=port)
            server.start()
            console.print(f"[cyan]Dashboard:[/cyan] {server.url}")
            if open_browser:
                import webbrowser

                webbrowser.open(server.url)

    guard = None
    if guardrails:
        from ..config.experiment import GuardrailConfig
        from ..live.guardrails import GuardrailEngine

        guard = GuardrailEngine(GuardrailConfig(enabled=True, warmup_days=world.config.warmup_days))

    # Inventory detail: explicit flag, else auto-enable for small scenarios.
    cfg = world.config
    if inventory_detail is None:
        inventory_detail = cfg.sku.n_sku * cfg.n_warehouses * cfg.sim_days <= 2_000_000

    engine = SimEngine(
        world, orders, seed, sourcing, replenishment,
        live_hub=hub, snapshot_every_hours=snapshot_every_hours, guardrail=guard,
        inventory_detail=inventory_detail,
    )
    result = engine.run(out_dir)
    if guard is not None and guard.triggered:
        console.print(f"[yellow]guardrail:[/yellow] {guard.triggered[0]}")
    metrics = write_metrics(
        out_dir, warmup_days=world.config.warmup_days, scenario_dir=scenario_dir
    )
    inv = validate(out_dir)

    if server is not None:
        console.print(
            f"[cyan]Run finished ({result['status']}).[/cyan] Dashboard is open — Ctrl+C to exit."
        )
        try:
            import time

            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            server.stop()

    if json_out:
        typer.echo(json.dumps({"out": str(out_dir), "metrics": metrics, "invariants": inv}))
    else:
        mark = "[green]✓[/green]" if inv["ok"] else "[red]✗[/red]"
        console.print(f"{mark} Run: {out_dir}  (policy: {sourcing})")
        console.print(f"  orders: {metrics['n_orders']}")
        cpo = metrics["total_cost_per_order"]
        console.print(f"  cost/order: {cpo:.3f}" if cpo else "  cost/order: —")
        hr = metrics["promise_hit_rate"]
        console.print(f"  on-time rate: {hr:.3f}" if hr is not None else "  on-time rate: —")
        console.print(f"  split rate: {metrics['split_rate']}")
        gap = metrics.get("gap_to_hindsight")
        if gap is not None:
            console.print(f"  gap to hindsight: {gap * 100:.1f}%")
        console.print(f"  invariants: {'OK' if inv['ok'] else inv['violations']}")


@app.command()
def validate(
    run_dir: Path = typer.Argument(..., help="run directory"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Validate the run invariants."""
    from ..core.invariants import validate as _validate

    res = _validate(run_dir)
    if json_out:
        typer.echo(json.dumps(res))
    else:
        mark = "[green]✓ OK[/green]" if res["ok"] else "[red]✗ VIOLATIONS[/red]"
        console.print(mark)
        for v in res["violations"]:
            console.print(f"  - {v}")
    raise typer.Exit(0 if res["ok"] else 1)


@app.command()
def compare(
    run_dirs: list[Path] = typer.Argument(..., help="policy run_dirs (e.g. runs/small_s*/nearest)"),
    baseline: str = typer.Option(..., "--baseline", help="baseline policy name"),
    out: Path = typer.Option(Path("compare"), "--out"),
    bootstrap: int = typer.Option(10_000, "--bootstrap"),
    ci: float = typer.Option(0.95, "--ci"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Compare policies pairwise against a baseline across seeds (t-CI + bootstrap)."""
    from ..metrics.compare import write_compare

    df = write_compare(
        run_dirs, baseline, out, ci_level=ci, bootstrap_iters=bootstrap, bootstrap_seed=12345
    )
    if json_out:
        typer.echo(df.to_json(orient="records"))
        return
    if df.empty:
        console.print("[yellow]No comparable metrics found.[/yellow]")
        return
    from rich.table import Table

    key = "total_cost_per_order"
    sub = df[df["metric"] == key]
    table = Table(title=f"Paired diff vs {baseline}  (metric: {key})")
    for col in ("policy", "n_seeds", "mean_diff", "t_ci_low", "t_ci_high", "significant_95"):
        table.add_column(col)
    for _, r in sub.iterrows():
        better = r["mean_diff"] < 0  # lower cost is better
        color = ("green" if better else "red") if r["significant_95"] else "white"
        table.add_row(
            r["policy"], str(int(r["n_seeds"])), f"[{color}]{r['mean_diff']:.3f}[/{color}]",
            f"{r['t_ci_low']:.3f}", f"{r['t_ci_high']:.3f}", str(bool(r["significant_95"])),
        )
    console.print(table)
    console.print(f"Full table: {out}/compare.csv")


@app.command()
def report(
    run_dir: Path = typer.Argument(..., help="a policy run_dir with events.parquet"),
    out: Path = typer.Option(None, "--out", help="output HTML (default <run_dir>/report.html)"),
    open_browser: bool = typer.Option(False, "--open"),
) -> None:
    """Build a static offline HTML report for a single run."""
    from ..reports.builder import build_report

    try:
        path = build_report(run_dir, out)
    except FileNotFoundError as e:
        console.print(f"[red]✗[/red] {e}")
        raise typer.Exit(1) from None
    console.print(f"[green]✓[/green] Report: {path}")
    if open_browser:
        import webbrowser

        webbrowser.open(f"file://{path.resolve()}")


@app.command()
def replay(
    run_dir: Path = typer.Argument(..., help="a policy run_dir with events.parquet"),
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8751, "--port"),
    open_browser: bool = typer.Option(True, "--open/--no-open"),
) -> None:
    """Open the offline replay viewer for a saved run (map, time slider, order chronology)."""
    from ..replay.loader import ReplayData
    from ..replay.server import ReplayServer

    if not (Path(run_dir) / "events.parquet").exists():
        console.print(
            f"[red]✗[/red] no events.parquet in {run_dir}. Run the simulation first:\n"
            f"  osbench run --scenario {Path(run_dir).parent} --sourcing {Path(run_dir).name}"
        )
        raise typer.Exit(1)
    data = ReplayData(run_dir)
    server = ReplayServer(data, host=host, port=port)
    console.print(f"[cyan]Replay:[/cyan] {server.url}  (Ctrl+C to exit)")
    if open_browser:
        import webbrowser

        webbrowser.open(server.url)
    try:
        server.start(background=False)
    except KeyboardInterrupt:
        pass


@app.command()
def hindsight(
    scenario: str = typer.Argument(..., help="a generated scenario run_dir (or preset/YAML)"),
    seed: int = typer.Option(0, "--seed", "-s"),
    warmup_days: int = typer.Option(14, "--warmup-days"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Compute the offline hindsight shipping lower bound for a scenario."""
    from ..metrics.hindsight import hindsight_cost

    scen = _resolve_scenario(scenario, seed)
    h = hindsight_cost(scen, warmup_days)
    if json_out:
        typer.echo(json.dumps(h))
    else:
        console.print(f"[cyan]Hindsight bound[/cyan] for {scen}")
        console.print(f"  shipping lower bound: {h['hindsight_shipping']}")
        console.print(f"  per order: {h['hindsight_per_order']}  (orders: {h['n_orders']})")


@app.command()
def sweep(
    config: Path = typer.Option(..., "--config", "-c", help="experiment YAML"),
    out: Path = typer.Option(Path("sweeps/run"), "--out"),
    workers: int = typer.Option(0, "--workers", help="0 = os.cpu_count()-1"),
    seeds: str = typer.Option(None, "--seeds", help="override, e.g. 0-9 or 0,1,2"),
    baseline: str = typer.Option(None, "--baseline"),
    keep_events: bool = typer.Option(False, "--keep-events/--no-keep-events"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Run a scenarios x policies x seeds sweep and build paired tables + a report."""
    import os

    import yaml

    from ..experiments.grid import expand_grid
    from ..experiments.sweep import run_sweep
    from ..metrics.compare import compare_sweep
    from ..reports.sweep_report import build_sweep_report

    spec = yaml.safe_load(Path(config).read_text(encoding="utf-8"))
    scenarios: list = [(s, s, {}) for s in spec.get("scenarios", [])]
    grid = spec.get("grid")
    if grid:
        scenarios += expand_grid(grid["base"], grid["axes"])
    if not scenarios:
        raise typer.BadParameter("experiment must define 'scenarios' and/or 'grid'")
    policies = spec["policies"]
    replenishment = spec.get("replenishment", "base_stock")
    base = baseline or spec.get("baseline", policies[0])
    seed_list = _parse_seeds(seeds) if seeds else spec.get("seeds", list(range(5)))
    nworkers = workers or max(1, (os.cpu_count() or 2) - 1)

    console.print(f"[cyan]Sweep[/cyan] {len(scenarios)} scenarios x {len(policies)} policies x "
                  f"{len(seed_list)} seeds on {nworkers} workers")
    done = {"n": 0}

    def _prog(i, total):
        if i != done["n"]:
            done["n"] = i
            console.print(f"  {i}/{total} tasks", end="\r")

    df = run_sweep(scenarios, policies, seed_list, out, replenishment=replenishment,
                   workers=nworkers, keep_events=keep_events, progress=_prog)
    console.print()
    errors = int((df["policy"] == "__error__").sum()) if "policy" in df else 0
    cmp = compare_sweep(df, base)
    cmp.to_csv(Path(out) / "compare.csv", index=False)
    report = build_sweep_report(Path(out) / "sweep.parquet", base)

    if json_out:
        typer.echo(json.dumps({"out": str(out), "rows": len(df), "errors": errors,
                               "report": str(report)}))
    else:
        console.print(f"[green]✓[/green] sweep → {out}/sweep.parquet ({len(df)} rows, {errors} errors)")
        console.print(f"  paired table → {out}/compare.csv")
        console.print(f"  report → {report}")


def _parse_seeds(spec: str) -> list[int]:
    if "-" in spec and "," not in spec:
        a, b = spec.split("-")
        return list(range(int(a), int(b) + 1))
    return [int(x) for x in spec.split(",")]


@app.command()
def tune(
    scenario: str = typer.Option(..., "--scenario", help="preset name or YAML config"),
    seeds: str = typer.Option("0-4", "--seeds", help="training seeds, e.g. 0-9 or 0,1,2"),
    trials: int = typer.Option(40, "--trials", help="weight vectors to try"),
    replenishment: str = typer.Option("base_stock", "--replenishment"),
    out: Path = typer.Option(Path("weights.json"), "--out", help="write the tuned weights here"),
    tune_seed: int = typer.Option(0, "--tune-seed"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Tune weighted_score factor weights on training seeds (RQ1); writes weights.json."""
    from ..experiments.tune import tune_weighted_score

    seed_list = _parse_seeds(seeds)
    res = tune_weighted_score(scenario, seed_list, trials, replenishment, tune_seed)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"weights": res["best_weights"], "cost": res["best_cost"],
                               "scenario": scenario, "seeds": seed_list}, indent=2), encoding="utf-8")
    baseline = res["trials"][0]["cost"]
    if json_out:
        typer.echo(json.dumps({"best_cost": res["best_cost"], "baseline_cost": baseline,
                               "out": str(out), "weights": res["best_weights"]}))
    else:
        gain = (baseline - res["best_cost"]) / baseline * 100 if baseline else 0.0
        console.print(f"[green]tuned[/green] cost/order {baseline:.3f} → {res['best_cost']:.3f}"
                      f" ({gain:+.1f}%) over {res['n_trials']} trials")
        console.print(f"  weights: {res['best_weights']}")
        console.print(f"  saved: {out}")


@app.command()
def studio(
    configs_dir: Path = typer.Option(Path("scenarios"), "--configs-dir",
                                     help="directory for editable user configs"),
    runs_dir: Path = typer.Option(Path("runs"), "--runs-dir"),
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8760, "--port"),
    open_browser: bool = typer.Option(True, "--open/--no-open"),
) -> None:
    """Open the visual config studio: edit / clone / preview / run scenario configs."""
    from ..studio.server import StudioServer

    configs_dir.mkdir(parents=True, exist_ok=True)
    server = StudioServer(configs_dir, runs_dir, host=host, port=port)
    console.print(f"[cyan]Studio:[/cyan] {server.url}  (Ctrl+C to exit)")
    if open_browser:
        import webbrowser

        webbrowser.open(server.url)
    try:
        server.start()
    except KeyboardInterrupt:
        pass


@app.command()
def policies() -> None:
    """Show the registered policies."""
    console.print(f"sourcing: {list_sourcing()}")
    console.print(f"replenishment: {list_replenishment()}")


if __name__ == "__main__":
    app()
