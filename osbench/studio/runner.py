"""Background run launcher for the studio, with live progress snapshots.

Each run gets its own LiveHub (the same snapshot mechanism the live dashboard uses). The
studio polls ``/api/run/{id}/snapshots`` for progress charts, and can stop a run. Artifacts
land under ``runs/<config>_s<seed>/<sourcing>``.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path

from ..config.loader import load_scenario
from ..config.schema import ScenarioConfig
from ..live.control import ControlFlags
from ..live.hub import LiveHub
from .configs import resolve_for_run


def _safe_label(name: str, fallback: str) -> str:
    """Filesystem-safe run label: keep word chars/.=- , collapse the rest to '_'."""
    label = re.sub(r"[^\w.=-]+", "_", (name or "").strip()).strip("_.")
    return label or fallback


class RunManager:
    def __init__(self, configs_dir: Path, runs_dir: Path = Path("runs")):
        self.configs_dir = Path(configs_dir)
        self.runs_dir = Path(runs_dir)
        self._runs: dict[str, dict] = {}
        self._live: dict[str, dict] = {}  # run_id -> {hub, control}
        self._lock = threading.Lock()

    def launch(self, config_name: str, sourcing: str, seed: int,
               replenishment: str = "base_stock", run_name: str | None = None,
               config_json: dict | None = None,
               sourcing_params: dict | None = None,
               replenishment_params: dict | None = None, mode: str = "full") -> str:
        """mode: "full" = generate then simulate, "generate" = world + orders only,
        "run" = simulate over an already generated folder ``runs/<run_name>`` (its own
        scenario.json is used; the editor config is ignored)."""
        if mode not in ("full", "generate", "run"):
            raise ValueError(f"unknown mode {mode!r}")
        run_id = uuid.uuid4().hex[:12]
        if mode == "run":
            label = _safe_label(run_name, "")
            scen_dir = self.runs_dir / label
            if not label or not (scen_dir / "orders.parquet").exists():
                raise FileNotFoundError(f"no generated data in {scen_dir}")
            cfg = ScenarioConfig.model_validate_json((scen_dir / "scenario.json").read_text("utf-8"))
            ref = cfg
            config_name = cfg.name
        # The run can carry an inline edited config (so tweaked params actually apply, even
        # for a preset) or just reference a saved config/preset by name.
        elif config_json is not None:
            ref: str | ScenarioConfig = ScenarioConfig.model_validate(config_json)
            cfg = ref
        else:
            ref = resolve_for_run(config_name, self.configs_dir)
            cfg = load_scenario(ref)
        if mode != "run":
            # The run label is its folder under runs/ -> a distinct name is a distinct entry and
            # never overwrites a run made with other parameters. Default: <config>_s<seed>.
            label = _safe_label(run_name, f"{config_name}_s{seed}")
        meta = {
            "preset": label, "policy": sourcing,
            "wh_names": [w.name for w in cfg.warehouses],
            "wh_x": [w.x for w in cfg.warehouses], "wh_y": [w.y for w in cfg.warehouses],
            "region_x": [r.x for r in cfg.regions], "region_y": [r.y for r in cfg.regions],
        }
        control = ControlFlags()
        hub = LiveHub(meta, control)
        with self._lock:
            self._runs[run_id] = {
                "run_id": run_id, "status": "starting", "config": config_name,
                "run_name": label, "sourcing": sourcing, "seed": seed, "mode": mode,
                "run_dir": None, "error": None, "started": time.time(),
            }
            self._live[run_id] = {"hub": hub, "control": control}
        threading.Thread(
            target=self._execute,
            args=(run_id, ref, label, sourcing, seed, replenishment,
                  sourcing_params or {}, replenishment_params or {}, mode),
            daemon=True,
        ).start()
        return run_id

    def _execute(self, run_id, ref, label, sourcing, seed, replenishment,
                 sourcing_params, replenishment_params, mode="full") -> None:
        try:
            import pandas as pd

            from ..core.engine import SimEngine
            from ..generate import generate_run, load_world_from_dir
            from ..metrics.compute import write_metrics

            scen_dir = self.runs_dir / label
            if mode != "run":
                self._update(run_id, status="generating")
                generate_run(ref, seed, scen_dir)
                if mode == "generate":
                    self._update(run_id, status="done", gen_dir=str(scen_dir))
                    return

            world = load_world_from_dir(scen_dir)
            orders = pd.read_parquet(scen_dir / "orders.parquet")
            out = scen_dir / sourcing
            cfg = world.config
            detail = cfg.sku.n_sku * cfg.n_warehouses * cfg.sim_days <= 2_000_000
            self._update(run_id, status="running", run_dir=str(out))
            hub = self._live[run_id]["hub"]
            from ..config.policy import ReplenishmentPolicyConfig, SourcingPolicyConfig
            scfg = SourcingPolicyConfig(name=sourcing, **sourcing_params) if sourcing_params else None
            rcfg = (ReplenishmentPolicyConfig(name=replenishment, **replenishment_params)
                    if replenishment_params else None)
            eng = SimEngine(world, orders, seed, sourcing, replenishment,
                            sourcing_cfg=scfg, replenishment_cfg=rcfg,
                            live_hub=hub, snapshot_every_hours=6, inventory_detail=detail)
            result = eng.run(out)
            # Run-time policy params, so the inspector can recompute safety stock / ATP.
            (out / "policy.json").write_text(json.dumps({
                "sourcing": sourcing, "replenishment": replenishment,
                "sourcing_params": sourcing_params, "replenishment_params": replenishment_params,
            }, indent=2), encoding="utf-8")
            metrics = write_metrics(out, warmup_days=cfg.warmup_days, scenario_dir=scen_dir)
            status = "stopped" if result.get("status") == "stopped" else "done"
            self._update(run_id, status=status, metrics=metrics)
        except Exception as e:  # noqa: BLE001
            self._update(run_id, status="failed", error=f"{type(e).__name__}: {e}")

    def _update(self, run_id, **fields) -> None:
        with self._lock:
            self._runs[run_id].update(fields)

    def status(self, run_id: str) -> dict:
        with self._lock:
            return dict(self._runs.get(run_id, {"run_id": run_id, "status": "unknown"}))

    def snapshots(self, run_id: str, since: int = -1) -> dict:
        st = self.status(run_id)
        live = self._live.get(run_id)
        if live is None:
            return {"status": st.get("status"), "meta": None, "snapshots": []}
        hub = live["hub"]
        return {
            "status": st.get("status"), "run_dir": st.get("run_dir"),
            "mode": st.get("mode"), "gen_dir": st.get("gen_dir"),
            "metrics": st.get("metrics"), "error": st.get("error"),
            "meta": hub.meta, "snapshots": hub.snapshots_since(since),
        }

    def stop(self, run_id: str) -> dict:
        live = self._live.get(run_id)
        if live is not None:
            live["control"].request_stop()
            return {"ok": True}
        return {"ok": False}

    def all(self) -> list[dict]:
        with self._lock:
            return sorted(self._runs.values(), key=lambda r: r["started"], reverse=True)
