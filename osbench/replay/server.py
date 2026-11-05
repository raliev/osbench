"""FastAPI server for the replay viewer (reads a saved run, no simulation)."""

from __future__ import annotations

import threading
from pathlib import Path

import orjson
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response

from .loader import ReplayData

_STATIC = Path(__file__).parent / "static"
_LIVE_STATIC = Path(__file__).parent.parent / "live" / "static"


def build_app(data: ReplayData) -> FastAPI:
    app = FastAPI()

    @app.get("/")
    def index() -> HTMLResponse:
        return HTMLResponse((_STATIC / "replay.html").read_text(encoding="utf-8"))

    @app.get("/replay.js")
    def js() -> Response:
        return Response(
            (_STATIC / "replay.js").read_text(encoding="utf-8"),
            media_type="application/javascript",
        )

    @app.get("/plotly.min.js")
    def plotly() -> Response:
        return Response(
            (_LIVE_STATIC / "plotly.min.js").read_bytes(), media_type="application/javascript"
        )

    @app.get("/api/meta")
    def meta() -> Response:
        return Response(orjson.dumps(data.meta()))

    @app.get("/api/day/{day}")
    def day(day: int) -> Response:
        return Response(orjson.dumps(data.orders_on_day(day)))

    @app.get("/api/order/{order_id}")
    def order(order_id: int) -> Response:
        return Response(orjson.dumps(data.order_detail(order_id)))

    @app.get("/api/warehouse/{wh}")
    def warehouse(wh: int, day: int = 0) -> Response:
        return Response(orjson.dumps(data.warehouse_detail(wh, day)))

    @app.get("/api/sku/{sku}")
    def sku(sku: int) -> Response:
        return Response(orjson.dumps(data.sku_detail(sku)))

    return app


class ReplayServer:
    def __init__(self, data: ReplayData, host: str = "127.0.0.1", port: int = 8751):
        self.data = data
        self.host = host
        self.port = port
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    def start(self, background: bool = False) -> None:
        app = build_app(self.data)
        config = uvicorn.Config(app, host=self.host, port=self.port, log_level="warning")
        self._server = uvicorn.Server(config)
        if background:
            self._thread = threading.Thread(target=self._server.run, daemon=True)
            self._thread.start()
        else:
            self._server.run()

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"
