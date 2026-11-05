"""FastAPI server for the replay viewer (reads a saved run, no simulation)."""

from __future__ import annotations

import threading
from pathlib import Path

import orjson
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response

from .explore import Explorer
from .loader import ReplayData

_STATIC = Path(__file__).parent / "static"
_LIVE_STATIC = Path(__file__).parent.parent / "live" / "static"


def _opt_int(v: str | None) -> int | None:
    return int(v) if v not in (None, "") else None


def build_app(data: ReplayData) -> FastAPI:
    app = FastAPI()
    ex = Explorer(data)

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
        return Response(orjson.dumps(ex.warehouse_detail(wh, day)))

    @app.get("/api/products")
    def products(day: int = 0, q: str = "", category: str = "", stocked: str = "",
                 sort: str = "sku", desc: bool = False, offset: int = 0, limit: int = 50) -> Response:
        return Response(orjson.dumps(ex.products(day, q, _opt_int(category), stocked, sort, desc,
                                                 offset, limit)))

    @app.get("/api/product/{sku}")
    def product(sku: int) -> Response:
        return Response(orjson.dumps(ex.product_detail(sku)))

    @app.get("/api/warehouses")
    def warehouses(day: int = 0, offset: int = 0, limit: int = 50) -> Response:
        return Response(orjson.dumps(ex.warehouses(day, offset, limit)))

    @app.get("/api/sales")
    def sales(day_from: str = "", day_to: str = "") -> Response:
        return Response(orjson.dumps(ex.sales(_opt_int(day_from), _opt_int(day_to))))

    @app.get("/api/sales/skus")
    def sales_skus(day_from: str = "", day_to: str = "", q: str = "", category: str = "",
                   sort: str = "revenue", desc: bool = True, offset: int = 0,
                   limit: int = 50) -> Response:
        return Response(orjson.dumps(ex.sales_skus(_opt_int(day_from), _opt_int(day_to), q,
                                                   _opt_int(category), sort, desc, offset, limit)))

    @app.get("/api/orders")
    def orders(order_id: str = "", day_from: str = "", day_to: str = "", status: str = "",
               wh: str = "", region: str = "", split: str = "", sku: str = "",
               min_lines: str = "", sort: str = "order_id", desc: bool = False,
               offset: int = 0, limit: int = 50) -> Response:
        return Response(orjson.dumps(ex.orders(
            _opt_int(order_id), _opt_int(day_from), _opt_int(day_to), status, _opt_int(wh),
            _opt_int(region), split, _opt_int(sku), _opt_int(min_lines), sort, desc,
            offset, limit)))

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
