"""World and demand generation."""

from __future__ import annotations

from pathlib import Path

from ..config.loader import load_scenario
from ..config.schema import ScenarioConfig
from ..rng.streams import RngHub
from .demand import build_orders
from .world import World, build_world
from .writer import write_manifest, write_orders, write_scenario, write_world


def generate_run(scenario: str | ScenarioConfig, seed: int, out_dir: str | Path) -> Path:
    """Generate the world and orders into out_dir. Returns the run_dir path."""
    cfg = scenario if isinstance(scenario, ScenarioConfig) else load_scenario(scenario)
    out = Path(out_dir)
    hub = RngHub(seed)
    world = build_world(cfg, hub)
    orders = build_orders(world, cfg, hub)
    write_world(out, world)
    write_orders(out, orders)
    write_scenario(out, cfg)
    write_manifest(out, cfg, seed, extra={"n_orders_lines": int(len(orders))})
    return out


def load_world_from_dir(run_dir: str | Path) -> World:
    """Reconstruct a World from a generated run_dir."""
    import numpy as np
    import pandas as pd

    from ..config.schema import ScenarioConfig

    d = Path(run_dir)
    cfg = ScenarioConfig.model_validate_json((d / "scenario.json").read_text(encoding="utf-8"))
    regions = pd.read_parquet(d / "regions.parquet")
    warehouses = pd.read_parquet(d / "warehouses.parquet")
    skus = pd.read_parquet(d / "skus.parquet")
    suppliers = pd.read_parquet(d / "suppliers.parquet")
    assort_sparse = pd.read_parquet(d / "assortment.parquet")
    assortment = np.zeros((len(skus), len(warehouses)), dtype=np.uint8)
    assortment[assort_sparse["sku_id"].to_numpy(), assort_sparse["warehouse_id"].to_numpy()] = 1
    return World(
        regions=regions,
        warehouses=warehouses,
        skus=skus,
        suppliers=suppliers,
        assortment=assortment,
        config=cfg,
    )


__all__ = ["World", "build_world", "build_orders", "generate_run", "load_world_from_dir"]
