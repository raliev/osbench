"""List completed runs and manage on-demand inspector servers for the studio."""

from __future__ import annotations

import json
import socket
import threading
from pathlib import Path


def list_runs(runs_dir: Path) -> list[dict]:
    """Find run directories (``<config>_sX/<policy>/events.parquet``) with a short summary."""
    runs_dir = Path(runs_dir)
    out = []
    if not runs_dir.exists():
        return out
    for ev in runs_dir.glob("*/*/events.parquet"):
        run_dir = ev.parent
        scen = run_dir.parent
        try:
            mtime = ev.stat().st_mtime  # when the run finished writing its journal
        except OSError:
            mtime = 0.0
        item = {"run_dir": str(run_dir), "config": scen.name, "policy": run_dir.name,
                "mtime": mtime}
        mpath = run_dir / "metrics.json"
        if mpath.exists():
            try:
                m = json.loads(mpath.read_text(encoding="utf-8"))
                item["cost_per_order"] = m.get("total_cost_per_order")
                item["on_time"] = m.get("promise_hit_rate")
                item["gap"] = m.get("gap_to_hindsight")
            except Exception:  # noqa: BLE001
                pass
        out.append(item)
    out.sort(key=lambda r: r["mtime"], reverse=True)  # most recent run first
    return out


class CompareManager:
    """Runs several sourcing policies on one generated world (same orders = CRN) and
    collects each policy's metrics and per-day time series for side-by-side comparison."""

    def __init__(self, configs_dir: Path, runs_dir: Path):
        self.configs_dir = Path(configs_dir)
        self.runs_dir = Path(runs_dir)
        self._cmp: dict[str, dict] = {}
        self._lock = threading.Lock()

    def launch(self, config: str, policies: list[str], seed: int,
               replenishment: str = "base_stock") -> str:
        import uuid

        cid = uuid.uuid4().hex[:12]
        with self._lock:
            self._cmp[cid] = {
                "id": cid, "config": config, "policies": policies, "seed": seed,
                "status": "running", "done": [], "results": {}, "error": None,
            }
        threading.Thread(target=self._run, args=(cid, config, policies, seed, replenishment),
                         daemon=True).start()
        return cid

    def _run(self, cid, config, policies, seed, replenishment) -> None:
        try:
            import pandas as pd

            from ..core.engine import SimEngine
            from ..generate import generate_run, load_world_from_dir
            from ..metrics.compute import write_metrics
            from ..metrics.timeseries import daily_series
            from .configs import resolve_for_run

            ref = resolve_for_run(config, self.configs_dir)
            scen_dir = self.runs_dir / f"{config}_s{seed}"
            generate_run(ref, seed, scen_dir)
            world = load_world_from_dir(scen_dir)
            orders = pd.read_parquet(scen_dir / "orders.parquet")
            cfg = world.config
            detail = cfg.sku.n_sku * cfg.n_warehouses * cfg.sim_days <= 2_000_000
            for pol in policies:
                out = scen_dir / pol
                SimEngine(world, orders, seed, pol, replenishment, inventory_detail=detail).run(out)
                m = write_metrics(out, warmup_days=cfg.warmup_days, scenario_dir=scen_dir)
                series = daily_series(out)
                with self._lock:
                    self._cmp[cid]["results"][pol] = {
                        "metrics": m, "series": series, "run_dir": str(out)}
                    self._cmp[cid]["done"].append(pol)
            with self._lock:
                self._cmp[cid]["status"] = "done"
        except Exception as e:  # noqa: BLE001
            with self._lock:
                self._cmp[cid]["status"] = "failed"
                self._cmp[cid]["error"] = f"{type(e).__name__}: {e}"

    def status(self, cid: str) -> dict:
        with self._lock:
            return dict(self._cmp.get(cid, {"id": cid, "status": "unknown"}))


class ComparisonStore:
    """Persisted comparisons: each is a named set of run_dirs you can reopen any time.

    Definitions live in ``<runs_dir>/comparisons.json``; per-run time series are computed
    on demand and cached in memory.
    """

    def __init__(self, runs_dir: Path):
        self.runs_dir = Path(runs_dir)
        self.path = self.runs_dir / "comparisons.json"
        self._series_cache: dict[str, dict] = {}
        self._lock = threading.Lock()

    def _load(self) -> list[dict]:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                return []
        return []

    def _save(self, items: list[dict]) -> None:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(items, indent=2), encoding="utf-8")

    def list(self) -> list[dict]:
        return self._load()

    def create(self, name: str, runs: list[str]) -> dict:
        import time
        import uuid

        with self._lock:
            items = self._load()
            item = {"id": uuid.uuid4().hex[:12], "name": name or "comparison",
                    "runs": [str(Path(r)) for r in runs], "created": time.time()}
            items.append(item)
            self._save(items)
        return item

    def delete(self, cid: str) -> dict:
        with self._lock:
            items = [i for i in self._load() if i["id"] != cid]
            self._save(items)
        return {"ok": True}

    def _series(self, run_dir: str) -> dict:
        if run_dir not in self._series_cache:
            from ..metrics.timeseries import daily_series

            self._series_cache[run_dir] = daily_series(run_dir)
        return self._series_cache[run_dir]

    def data(self, cid: str) -> dict:
        item = next((i for i in self._load() if i["id"] == cid), None)
        if item is None:
            return {"error": "not found"}
        runs = []
        for run_dir in item["runs"]:
            rp = Path(run_dir)
            if not (rp / "events.parquet").exists():
                continue
            scen = rp.parent
            info = {"run_dir": run_dir, "config": scen.name, "policy": rp.name,
                    "label": f"{scen.name}/{rp.name}"}
            mpath = rp / "metrics.json"
            info["metrics"] = json.loads(mpath.read_text("utf-8")) if mpath.exists() else {}
            info["series"] = self._series(run_dir)
            runs.append(info)
        return {"id": item["id"], "name": item["name"], "created": item["created"], "runs": runs}


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class InspectorManager:
    """Starts (and reuses) a replay inspector server per run_dir; returns its URL."""

    def __init__(self, host: str = "127.0.0.1"):
        self.host = host
        self._servers: dict[str, str] = {}  # run_dir -> url
        self._lock = threading.Lock()

    def url_for(self, run_dir: str) -> dict:
        run_dir = str(Path(run_dir))
        if not (Path(run_dir) / "events.parquet").exists():
            return {"error": f"no events.parquet in {run_dir}"}
        with self._lock:
            if run_dir in self._servers:
                return {"url": self._servers[run_dir]}
            from ..replay.loader import ReplayData
            from ..replay.server import ReplayServer

            port = _free_port()
            server = ReplayServer(ReplayData(run_dir), host=self.host, port=port)
            server.start(background=True)
            url = f"http://{self.host}:{port}"
            self._servers[run_dir] = url
            return {"url": url}
