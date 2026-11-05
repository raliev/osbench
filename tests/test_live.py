"""Live monitoring tests: snapshots, stop, guardrail, server protocol."""

from __future__ import annotations

import pandas as pd
import pytest

from osbench.config.experiment import GuardrailConfig
from osbench.core.engine import SimEngine
from osbench.generate import generate_run, load_world_from_dir
from osbench.live.control import ControlFlags
from osbench.live.guardrails import GuardrailEngine
from osbench.live.hub import LiveHub


def _world(tmp_path, preset="small"):
    gen = generate_run(preset, 0, tmp_path / "gen")
    world = load_world_from_dir(gen)
    orders = pd.read_parquet(gen / "orders.parquet")
    return world, orders


def _meta(world, policy):
    return {
        "preset": world.config.name, "policy": policy,
        "wh_names": world.warehouses["name"].tolist(),
        "wh_x": world.warehouses["x"].tolist(), "wh_y": world.warehouses["y"].tolist(),
        "region_x": world.regions["x"].tolist(), "region_y": world.regions["y"].tolist(),
    }


@pytest.mark.fast
def test_snapshots_emitted(tmp_path):
    world, orders = _world(tmp_path, "tiny")
    hub = LiveHub(_meta(world, "nearest"), ControlFlags())
    SimEngine(world, orders, 0, "nearest", live_hub=hub, snapshot_every_hours=6).run(tmp_path / "r")
    snaps = hub.all_snapshots()
    assert len(snaps) > 1
    assert {"seq", "sim_day", "cost_per_order", "wh", "compute"} <= set(snaps[0])
    assert len(snaps[0]["wh"]["util"]) == world.n_wh


@pytest.mark.fast
def test_warehouse_drill(tmp_path):
    world, orders = _world(tmp_path, "tiny")
    hub = LiveHub(_meta(world, "nearest"), ControlFlags())
    SimEngine(world, orders, 0, "nearest", live_hub=hub, snapshot_every_hours=6).run(tmp_path / "r")
    d = hub.warehouse_detail(0)
    assert d["type"] == "wh_detail"
    assert len(d["day"]) == len(d["stock"]) > 0


@pytest.mark.fast
def test_stop_from_ui(tmp_path):
    world, orders = _world(tmp_path, "small")
    ctrl = ControlFlags()
    ctrl.request_stop()  # stop before the start → halts on the very first tick
    hub = LiveHub(_meta(world, "nearest"), ctrl)
    out = tmp_path / "r"
    res = SimEngine(world, orders, 0, "nearest", live_hub=hub, snapshot_every_hours=6).run(out)
    assert res["status"] == "stopped"
    assert (out / "events.parquet").exists()  # partial metrics are saved


@pytest.mark.fast
def test_guardrail_aborts_broken_policy(tmp_path):
    world, orders = _world(tmp_path, "small")
    guard = GuardrailEngine(
        GuardrailConfig(enabled=True, warmup_days=3, mode="abort"), lost_sales_max=0.30
    )
    eng = SimEngine(world, orders, 0, "broken_wh0", snapshot_every_hours=6, guardrail=guard)
    res = eng.run(tmp_path / "r")
    assert res["status"] == "aborted"
    assert guard.triggered
    # Stopped early (by the rule) rather than playing out the whole horizon.
    assert eng._last_t // 1440 < world.config.sim_days


@pytest.mark.fast
def test_server_protocol(tmp_path):
    """WS protocol via TestClient: hello + snapshot + drill + stop."""
    from fastapi.testclient import TestClient

    from osbench.live.server import build_app

    world, _ = _world(tmp_path, "tiny")
    hub = LiveHub(_meta(world, "nearest"), ControlFlags())
    hub.emit({"seq": 0, "sim_day": 0, "wh": {"stock": [1, 2], "util": [0.1, 0.2],
              "queue": [0, 0], "inbound": [0, 0], "outbound": [0, 0]}})
    app = build_app(hub)
    client = TestClient(app)
    assert client.get("/healthz").json()["status"] == "ok"
    with client.websocket_connect("/ws") as ws:
        hello = ws.receive_json(mode="binary")
        assert hello["type"] == "hello" and hello["policy"] == "nearest"
        snap = ws.receive_json(mode="binary")
        assert snap["type"] == "snapshot"
        ws.send_json({"type": "drill", "wh": 0})
        detail = ws.receive_json(mode="binary")
        assert detail["type"] == "wh_detail"
        ws.send_json({"type": "control", "cmd": "stop"})
        assert hub.control.stop_requested
