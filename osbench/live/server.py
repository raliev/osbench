"""FastAPI server for the live dashboard (uvicorn in a background thread)."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import orjson
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response

from .hub import LiveHub

_STATIC = Path(__file__).parent / "static"


def build_app(hub: LiveHub) -> FastAPI:
    app = FastAPI()

    @app.get("/")
    def index() -> HTMLResponse:
        return HTMLResponse((_STATIC / "dashboard.html").read_text(encoding="utf-8"))

    @app.get("/plotly.min.js")
    def plotly() -> Response:
        return Response(
            (_STATIC / "plotly.min.js").read_bytes(), media_type="application/javascript"
        )

    @app.get("/dashboard.js")
    def dash_js() -> Response:
        return Response(
            (_STATIC / "dashboard.js").read_text(encoding="utf-8"),
            media_type="application/javascript",
        )

    @app.get("/healthz")
    def healthz() -> Response:
        return Response(orjson.dumps({"status": "ok", "seq": hub.latest_seq()}))

    @app.websocket("/ws")
    async def ws(sock: WebSocket) -> None:
        await sock.accept()
        await sock.send_bytes(orjson.dumps({"type": "hello", **hub.meta}))
        last_seq = -1
        # Send the accumulated backlog.
        for snap in hub.all_snapshots():
            await sock.send_bytes(orjson.dumps({"type": "snapshot", "data": snap}))
            last_seq = snap["seq"]

        async def pump():
            nonlocal last_seq
            while True:
                for snap in hub.snapshots_since(last_seq):
                    await sock.send_bytes(orjson.dumps({"type": "snapshot", "data": snap}))
                    last_seq = snap["seq"]
                if hub.finished:
                    await sock.send_bytes(
                        orjson.dumps({"type": "run_state", "state": hub.control.state})
                    )
                await asyncio.sleep(0.2)

        pump_task = asyncio.create_task(pump())
        try:
            while True:
                raw = await sock.receive_text()
                msg = orjson.loads(raw)
                mtype = msg.get("type")
                if mtype == "control" and msg.get("cmd") == "stop":
                    hub.control.request_stop()
                    await sock.send_bytes(orjson.dumps({"type": "run_state", "state": "stopping"}))
                elif mtype == "drill":
                    detail = hub.warehouse_detail(int(msg["wh"]))
                    await sock.send_bytes(orjson.dumps(detail))
        except WebSocketDisconnect:
            pass
        finally:
            pump_task.cancel()

    return app


class LiveServer:
    """Starts uvicorn in a daemon thread; does not block the core thread."""

    def __init__(self, hub: LiveHub, host: str = "127.0.0.1", port: int = 8750):
        self.hub = hub
        self.host = host
        self.port = port
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        app = build_app(self.hub)
        config = uvicorn.Config(app, host=self.host, port=self.port, log_level="warning")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"
