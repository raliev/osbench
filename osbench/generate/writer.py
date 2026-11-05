"""Writing the world, orders, and manifest to run_dir."""

from __future__ import annotations

import json
import platform
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from .. import __version__
from ..config.loader import config_hash
from ..config.schema import ScenarioConfig
from .world import World


def write_world(run_dir: Path, world: World) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    world.regions.to_parquet(run_dir / "regions.parquet", index=False)
    world.warehouses.to_parquet(run_dir / "warehouses.parquet", index=False)
    world.skus.to_parquet(run_dir / "skus.parquet", index=False)
    world.suppliers.to_parquet(run_dir / "suppliers.parquet", index=False)
    # Assortment — a sparse table of (sku, wh) pairs.
    sku_idx, wh_idx = np.nonzero(world.assortment)
    pd.DataFrame({"sku_id": sku_idx.astype(np.int64), "warehouse_id": wh_idx.astype(np.int32)}).to_parquet(
        run_dir / "assortment.parquet", index=False
    )


def write_orders(run_dir: Path, orders: pd.DataFrame) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    orders.to_parquet(run_dir / "orders.parquet", index=False)


def write_scenario(run_dir: Path, cfg: ScenarioConfig) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "scenario.json").write_text(
        cfg.model_dump_json(indent=2), encoding="utf-8"
    )


def write_manifest(run_dir: Path, cfg: ScenarioConfig, seed: int, extra: dict | None = None) -> None:
    manifest = {
        "osbench_version": __version__,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "node": platform.node(),
        "scenario": cfg.name,
        "config_hash": config_hash(cfg),
        "seed": int(seed),
        "sim_days": cfg.sim_days,
        "warmup_days": cfg.warmup_days,
    }
    if extra:
        manifest.update(extra)
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
