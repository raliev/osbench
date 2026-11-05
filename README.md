# osbench

An open, reproducible benchmark stand for comparing order-sourcing (warehouse-selection)
algorithms in an e-commerce network. Discrete-event simulator with common random numbers (CRN)
and a strict comparison protocol.

> Status: **v1 complete** — package skeleton, configs, RNG streams, world/demand generator,
> simulation core, journal, invariants, metrics, four sourcing policies (random, nearest,
> cheapest_myopic, weighted_score), base_stock/s_S replenishment, a live web dashboard,
> paired comparison, and offline HTML reports.
> Full spec: `plans/expressive-pondering-scroll.md`.

## Install

```bash
conda activate rapids-13
pip install -e .
```

## Quick start

The usual flow is **generate → run → analyze**. `report`, `replay`, and `compare` read the
output of a completed `run`, so run the simulation for a policy before reporting on it (and the
`--sourcing` name must match the directory you report/replay).

```bash
# 1) Generate a world and a pre-generated order stream (or let `run` do it automatically)
osbench generate --config small --seed 0 --out runs/small_s0/

# 2) Run the simulation with a sourcing policy -> writes runs/small_s0/nearest/
osbench run --scenario runs/small_s0/ --sourcing nearest --replenishment base_stock

# Run with the live web dashboard (map, drill-down by warehouse, stop button).
# Charts update as the run progresses; small runs finish in seconds, so also look at
# the final state. For a longer live view use more days or a bigger preset.
osbench run --scenario runs/small_s0/ --sourcing nearest --serve

# Check run invariants
osbench validate runs/small_s0/nearest/

# Compare policies pairwise across seeds (t-CI + bootstrap)
osbench compare runs/small_s*/nearest runs/small_s*/weighted_score --baseline nearest --out compare/

# Run a full experiment sweep (scenarios x policies x seeds, parallel) and build the
# paired-CI tables, per-seed scatter, Pareto, and a load x cover sensitivity heatmap.
osbench sweep --config experiments/demo_sweep.yaml --out sweeps/demo --workers 4

# Build a static offline HTML report for one run (run that policy first!)
osbench run --scenario runs/small_s0/ --sourcing weighted_score
osbench report runs/small_s0/weighted_score --open

# Open the offline inspector: order points on a map over time, click into an order
# (lines, qty, chosen warehouse, chronology) -> warehouse (stock/flow charts, top SKUs)
# -> SKU (per-warehouse stock, assigned/day). A time cursor tracks the slider.
# Per-SKU charts need per-SKU daily stock, written automatically for small scenarios
# (or force it with: osbench run ... --inventory-detail).
osbench replay runs/small_s0/nearest

# Offline hindsight shipping lower bound ("cost of myopia" reference).
# Every run's metrics.json also carries gap_to_hindsight.
osbench hindsight small

# Open the visual config studio: an interactive map editor (drag warehouses & customer
# circles, wheel-zoom/pan, click to edit with sliders), parameter sliders, a clickable
# order-lifecycle diagram, Clone/Save, and Run with a live progress monitor (cost, service,
# utilization, stockout charts + Stop). User configs are saved to scenarios/.
osbench studio

# List registered policies
osbench policies
```

Generation writes `regions.parquet`, `warehouses.parquet`, `skus.parquet`, `suppliers.parquet`,
`assortment.parquet`, `orders.parquet`, `scenario.json`, `manifest.json`. A run adds
`events.parquet`, `inventory_daily.parquet`, `metrics.json`.

## Reproducibility

A single master seed fans out into named independent RNG streams (`osbench/rng/streams.py`). Two
runs with the same seed see the same world and order stream. The simulation core is deterministic:
the event journal is byte-for-byte identical across runs (only wall-clock `policy_time_ms` differs,
so it is excluded from comparisons).

```bash
pytest -m fast      # fast unit tests (every commit)
pytest -m nightly   # golden run and plausibility checks (added in later stages)
```

## Presets

| preset | regions | warehouses | SKUs | orders/day | purpose |
|---|---|---|---|---|---|
| tiny | 2 | 2 | 50 | 20 | unit tests |
| small | 5 | 4 | 500 | 200 | main comparisons, golden |

### Demo & scenario configs

Each is a self-documented YAML under `osbench/config/presets/` (header comment explains
the case it covers). Use by name: `osbench run --scenario demo_contested --serve`.

| config | case |
|---|---|
| `demo_contested` | customers in a band between two warehouses — the cheaper/nearer warehouse flips across the midline (use the inspector's "color by warehouse") |
| `demo_two_cities` | two separate markets, each served locally; cross-city shipping is far and expensive |
| `demo_big_basket` | large multi-item baskets (~3.5 lines) + partial assortment → orders split across warehouses |
| `demo_scarce` | tight stock, slow replenishment, imbalanced demand → lost sales rise and the sourcing policy choice matters |
| `demo_heavy` | high-volume stress (~160k orders) for the dashboards, report, and compare |
| `demo_showcase` | capacity-bound symmetric warehouses + lateness penalty — a load-aware policy (`weighted_score`) beats `cheapest_myopic` by ~40% on total cost (clear demonstrable win) |
| `demo_stranded` | scarcity + ATP tolerance (`atp.tolerance_enabled`): a captive region can only be served from the central warehouse, so a myopic policy drains it on other regions and strands stock → lost captive sales. `weighted_score` preserves it and beats `cheapest_myopic`/`nearest` (~3% total cost, ~1.8pp fewer lost sales) |

**ATP network tolerance.** With `atp.tolerance_enabled: true`, a line is only sourced from
warehouses within `tolerance_price_frac` / `tolerance_days` of the best candidate; distant
stock becomes unreachable and can be *stranded*, so the sourcing allocation drives lost
sales (off by default — it does not change the abundant-stock regimes). This is the lever
behind `demo_stranded`.

## Live dashboard

`osbench run --serve` starts a local FastAPI + WebSocket server (uvicorn in a background daemon
thread) and a fully offline Plotly dashboard. Charts update incrementally as the run progresses;
clicking a warehouse on the map shows its per-warehouse series; a Stop button halts the run and
saves partial metrics. Guardrails can auto-stop a misbehaving policy (`--guardrails`).

## Adding a policy

One file + registration. Create a class with a `decide(order, ctx)` method, mark it with the
`@register_sourcing("name")` decorator (or declare an entry point in `pyproject.toml`), and the
policy becomes available via `osbench run --sourcing name`.

## License

MIT.
