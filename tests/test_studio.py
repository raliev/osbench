"""Tests for the config studio: config IO, preview, validation, clone, run."""

from __future__ import annotations

import time

import orjson
import pytest
from fastapi.testclient import TestClient

from osbench.studio import configs as cfgio
from osbench.studio.preview import preview
from osbench.studio.server import build_app


@pytest.fixture
def client(tmp_path):
    return TestClient(build_app(tmp_path / "scenarios", tmp_path / "runs"))


@pytest.mark.fast
def test_list_and_read(client):
    lst = client.get("/api/configs").json()
    assert "tiny" in lst["presets"] and "demo_contested" in lst["presets"]
    data = client.get("/api/config/tiny").json()
    assert data["source"] == "preset" and data["config"]["name"] == "tiny"


@pytest.mark.fast
def test_preview_faithful():
    data = cfgio.read_config("demo_contested", "/nonexistent")
    p = preview(data["config"], n_customers=300, seed=0)
    assert len(p["customers"]["x"]) == 300
    assert len(p["warehouses"]) == 2 and len(p["regions"]) == 3
    # Plane geometry labels each customer with a nearest warehouse (0 or 1).
    assert set(p["customers"]["nearest"]) <= {0, 1}


@pytest.mark.fast
def test_validate_reports_errors(client):
    data = client.get("/api/config/tiny").json()["config"]
    assert client.post("/api/validate", content=orjson.dumps(data)).json()["ok"]
    data["demand"]["base_orders_per_day"] = -1
    res = client.post("/api/validate", content=orjson.dumps(data)).json()
    assert not res["ok"] and any("base_orders_per_day" in e["loc"] for e in res["errors"])


@pytest.mark.fast
def test_clone_and_save(client, tmp_path):
    r = client.post("/api/clone", content=orjson.dumps({"src": "tiny", "dst": "mine"})).json()
    assert r["ok"]
    assert (tmp_path / "scenarios" / "mine.yaml").exists()
    assert "mine" in client.get("/api/configs").json()["user"]
    # Edit and save.
    data = client.get("/api/config/mine").json()["config"]
    data["sim_days"] = 12
    s = client.post("/api/save", content=orjson.dumps({"name": "mine", "config": data})).json()
    assert s["ok"]
    assert client.get("/api/config/mine").json()["config"]["sim_days"] == 12


@pytest.mark.fast
def test_description_round_trips(client):
    # Preset descriptions are exposed, and an edited description persists through save.
    assert client.get("/api/config/demo_showcase").json()["config"]["description"]
    client.post("/api/clone", content=orjson.dumps({"src": "tiny", "dst": "described"}))
    data = client.get("/api/config/described").json()["config"]
    data["description"] = "special: captive region demo\n[2026-11-05 10:00] changed: sim_days: 30 → 45"
    s = client.post("/api/save", content=orjson.dumps({"name": "described", "config": data})).json()
    assert s["ok"]
    back = client.get("/api/config/described").json()["config"]["description"]
    assert "captive region demo" in back and "changed: sim_days" in back


@pytest.mark.fast
def test_run_launch_and_complete(client):
    client.post("/api/clone", content=orjson.dumps({"src": "tiny", "dst": "runme"})).json()
    run_id = client.post(
        "/api/run", content=orjson.dumps({"config": "runme", "sourcing": "nearest", "seed": 0})
    ).json()["run_id"]
    status = None
    for _ in range(60):
        status = client.get("/api/run/" + run_id).json()
        if status["status"] in ("done", "failed"):
            break
        time.sleep(0.5)
    assert status["status"] == "done", status
    assert status["metrics"]["total_cost_per_order"] > 0


def _await_done(client, run_id, tries=60):
    status = None
    for _ in range(tries):
        status = client.get("/api/run/" + run_id).json()
        if status["status"] in ("done", "failed"):
            return status
        time.sleep(0.5)
    return status


@pytest.mark.fast
def test_named_runs_do_not_overwrite(client):
    # A base config and the same config with an edited param, each with its own run name,
    # must produce two distinct run directories (no overwrite) that both complete.
    base = client.get("/api/config/tiny").json()["config"]
    edited = {**base, "initial_cover_days": base["initial_cover_days"] + 7}

    r1 = client.post("/api/run", content=orjson.dumps(
        {"config": "tiny", "sourcing": "nearest", "seed": 0,
         "run_name": "tiny_base", "config_json": base})).json()
    r2 = client.post("/api/run", content=orjson.dumps(
        {"config": "tiny", "sourcing": "nearest", "seed": 0,
         "run_name": "tiny_cover_edited", "config_json": edited})).json()

    s1 = _await_done(client, r1["run_id"])
    s2 = _await_done(client, r2["run_id"])
    assert s1["status"] == "done" and s2["status"] == "done"
    # Distinct folders -> separate entries, neither clobbers the other.
    assert s1["run_dir"] != s2["run_dir"]
    assert "tiny_base" in s1["run_dir"] and "tiny_cover_edited" in s2["run_dir"]
    runs = {r["run_dir"] for r in client.get("/api/results").json()}
    assert s1["run_dir"] in runs and s2["run_dir"] in runs


@pytest.mark.fast
def test_run_rejects_invalid_inline_config(client):
    bad = {**client.get("/api/config/tiny").json()["config"], "sim_days": -5}
    r = client.post("/api/run", content=orjson.dumps(
        {"config": "tiny", "sourcing": "nearest", "seed": 0, "config_json": bad})).json()
    assert "error" in r and "run_id" not in r


@pytest.mark.fast
def test_run_snapshots_stream(client):
    client.post("/api/clone", content=orjson.dumps({"src": "tiny", "dst": "snap"}))
    run_id = client.post(
        "/api/run", content=orjson.dumps({"config": "snap", "sourcing": "nearest", "seed": 0})
    ).json()["run_id"]
    seen, last, done = 0, -1, False
    for _ in range(120):
        s = client.get(f"/api/run/{run_id}/snapshots?since={last}").json()
        assert s["meta"] is not None and "wh_names" in s["meta"]
        for snap in s["snapshots"]:
            assert {"sim_day", "cost_per_order", "wh", "compute"} <= set(snap)
            seen += 1
            last = snap["seq"]
        if s["status"] in ("done", "failed", "stopped"):
            done = True
            break
        time.sleep(0.2)
    assert done and seen > 0


@pytest.mark.fast
def test_run_stop(client):
    client.post("/api/clone", content=orjson.dumps({"src": "small", "dst": "stopme"}))
    run_id = client.post(
        "/api/run", content=orjson.dumps({"config": "stopme", "sourcing": "nearest", "seed": 0})
    ).json()["run_id"]
    # Wait until it is actually running, then stop.
    for _ in range(40):
        if client.get(f"/api/run/{run_id}").json()["status"] == "running":
            break
        time.sleep(0.1)
    assert client.post(f"/api/run/{run_id}/stop").json()["ok"]
    status = None
    for _ in range(80):
        status = client.get(f"/api/run/{run_id}").json()["status"]
        if status in ("stopped", "done", "failed"):
            break
        time.sleep(0.2)
    assert status in ("stopped", "done")  # stopped if caught mid-run


@pytest.mark.fast
def test_compare_policies(client):
    client.post("/api/clone", content=orjson.dumps({"src": "tiny", "dst": "cmp"}))
    cid = client.post("/api/compare", content=orjson.dumps(
        {"config": "cmp", "policies": ["nearest", "cheapest_myopic"], "seed": 0}
    )).json()["id"]
    s = None
    for _ in range(120):
        s = client.get("/api/compare/" + cid).json()
        if s["status"] in ("done", "failed"):
            break
        time.sleep(0.3)
    assert s["status"] == "done", s
    assert set(s["results"]) == {"nearest", "cheapest_myopic"}
    for pol, r in s["results"].items():
        assert r["metrics"]["total_cost_per_order"] > 0
        assert len(r["series"]["days"]) > 0
        assert "on_time_rate" in r["series"]["series"]


@pytest.mark.fast
def test_analysis_endpoint(client):
    cid = None
    run_id = client.post("/api/run", content=orjson.dumps(
        {"config": "tiny", "sourcing": "nearest", "seed": 0}
    )).json()["run_id"]
    for _ in range(60):
        st = client.get("/api/run/" + run_id).json()
        if st["status"] == "done":
            break
        time.sleep(0.3)
    a = client.get("/api/analysis?run=" + st["run_dir"]).json()
    assert a["days"] and {"created", "delivered", "on_time_rate", "shipping"} <= set(a["series"])
