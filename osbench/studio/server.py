"""FastAPI server for the config studio (editor + preview + launcher)."""

from __future__ import annotations

from pathlib import Path

import orjson
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response

from . import configs as cfgio
from .preview import preview
from .results import (ComparisonStore, CompareManager, InspectorManager, list_generations,
                      list_runs)
from .runner import RunManager

_STATIC = Path(__file__).parent / "static"
_LIVE_STATIC = Path(__file__).parent.parent / "live" / "static"


def _json(obj) -> Response:
    return Response(orjson.dumps(obj), media_type="application/json")


def build_app(configs_dir: Path, runs_dir: Path = Path("runs")) -> FastAPI:
    app = FastAPI()
    configs_dir = Path(configs_dir)
    runs_dir = Path(runs_dir)
    runner = RunManager(configs_dir, runs_dir)
    inspector = InspectorManager()
    comparer = CompareManager(configs_dir, runs_dir)
    comparisons = ComparisonStore(runs_dir)

    # Never cache the app shell/assets, so edits always take effect on reload.
    _NOCACHE = {"Cache-Control": "no-store, max-age=0"}

    @app.get("/")
    def index() -> HTMLResponse:
        return HTMLResponse((_STATIC / "studio.html").read_text(encoding="utf-8"), headers=_NOCACHE)

    @app.get("/studio.js")
    def js() -> Response:
        return Response((_STATIC / "studio.js").read_text("utf-8"),
                        media_type="application/javascript", headers=_NOCACHE)

    @app.get("/studio.css")
    def css() -> Response:
        return Response((_STATIC / "studio.css").read_text("utf-8"),
                        media_type="text/css", headers=_NOCACHE)

    @app.get("/plotly.min.js")
    def plotly() -> Response:
        return Response((_LIVE_STATIC / "plotly.min.js").read_bytes(),
                        media_type="application/javascript")

    @app.get("/api/configs")
    def list_configs() -> Response:
        return _json(cfgio.list_configs(configs_dir))

    @app.get("/api/config/{name}")
    def get_config(name: str) -> Response:
        try:
            return _json(cfgio.read_config(name, configs_dir))
        except FileNotFoundError as e:
            return _json({"error": str(e)})

    @app.post("/api/validate")
    async def validate(req: Request) -> Response:
        body = orjson.loads(await req.body())
        return _json(cfgio.validate_config(body))

    @app.post("/api/preview")
    async def do_preview(req: Request) -> Response:
        body = orjson.loads(await req.body())
        try:
            return _json(preview(body.get("config", body), body.get("n", 500), body.get("seed", 0)))
        except Exception as e:  # noqa: BLE001
            return _json({"error": f"{type(e).__name__}: {e}"})

    @app.post("/api/save")
    async def save(req: Request) -> Response:
        body = orjson.loads(await req.body())
        return _json(cfgio.save_config(body["name"], body["config"], configs_dir))

    @app.post("/api/clone")
    async def clone(req: Request) -> Response:
        body = orjson.loads(await req.body())
        return _json(cfgio.clone_config(body["src"], body["dst"], configs_dir))

    @app.post("/api/run")
    async def run(req: Request) -> Response:
        body = orjson.loads(await req.body())
        try:
            run_id = runner.launch(
                body["config"], body.get("sourcing", "nearest"), int(body.get("seed", 0)),
                body.get("replenishment", "base_stock"),
                run_name=body.get("run_name"), config_json=body.get("config_json"),
                sourcing_params=body.get("sourcing_params"),
                replenishment_params=body.get("replenishment_params"),
                mode=body.get("mode", "full"),
            )
        except Exception as e:  # noqa: BLE001 -- surface a bad inline config as a message
            return _json({"error": f"{type(e).__name__}: {e}"})
        return _json({"run_id": run_id})

    @app.get("/api/run/{run_id}")
    def run_status(run_id: str) -> Response:
        return _json(runner.status(run_id))

    @app.get("/api/run/{run_id}/snapshots")
    def run_snapshots(run_id: str, since: int = -1) -> Response:
        return _json(runner.snapshots(run_id, since))

    @app.post("/api/run/{run_id}/stop")
    def run_stop(run_id: str) -> Response:
        return _json(runner.stop(run_id))

    @app.get("/api/runs")
    def runs() -> Response:
        return _json(runner.all())

    @app.get("/api/policies")
    def policies() -> Response:
        from ..registry import list_replenishment, list_sourcing

        return _json({"sourcing": list_sourcing(), "replenishment": list_replenishment()})

    @app.get("/api/generations")
    def generations() -> Response:
        return _json(list_generations(runs_dir))

    @app.get("/api/results")
    def results() -> Response:
        return _json(list_runs(runs_dir))

    @app.get("/api/inspect")
    def inspect(run: str) -> Response:
        return _json(inspector.url_for(run))

    @app.get("/api/analysis")
    def analysis(run: str) -> Response:
        from ..metrics.timeseries import daily_series

        try:
            return _json(daily_series(run))
        except Exception as e:  # noqa: BLE001
            return _json({"error": f"{type(e).__name__}: {e}"})

    @app.post("/api/compare")
    async def compare(req: Request) -> Response:
        body = orjson.loads(await req.body())
        cid = comparer.launch(body["config"], body["policies"], int(body.get("seed", 0)),
                              body.get("replenishment", "base_stock"))
        return _json({"id": cid})

    @app.get("/api/compare/{cid}")
    def compare_status(cid: str) -> Response:
        return _json(comparer.status(cid))

    @app.get("/api/runinfo")
    def runinfo(run: str) -> Response:
        from ..metrics.timeseries import daily_series

        rp = Path(run)
        metrics = {}
        mp = rp / "metrics.json"
        if mp.exists():
            metrics = orjson.loads(mp.read_bytes())
        try:
            series = daily_series(run)
        except Exception as e:  # noqa: BLE001
            return _json({"error": f"{type(e).__name__}: {e}"})
        return _json({"run_dir": run, "metrics": metrics, "series": series})

    @app.get("/api/comparisons")
    def comparisons_list() -> Response:
        return _json(comparisons.list())

    @app.post("/api/comparisons")
    async def comparisons_create(req: Request) -> Response:
        body = orjson.loads(await req.body())
        return _json(comparisons.create(body.get("name", "comparison"), body["runs"]))

    @app.get("/api/comparisons/{cid}")
    def comparisons_get(cid: str) -> Response:
        return _json(comparisons.data(cid))

    @app.delete("/api/comparisons/{cid}")
    def comparisons_delete(cid: str) -> Response:
        return _json(comparisons.delete(cid))

    return app


class StudioServer:
    def __init__(self, configs_dir: Path, runs_dir: Path = Path("runs"),
                 host: str = "127.0.0.1", port: int = 8760):
        self.app = build_app(configs_dir, runs_dir)
        self.host = host
        self.port = port

    def start(self) -> None:
        uvicorn.run(self.app, host=self.host, port=self.port, log_level="warning")

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"
