"""Tests for the inspector: data loader and server endpoints."""

from __future__ import annotations

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from osbench.core.engine import SimEngine
from osbench.generate import generate_run, load_world_from_dir
from osbench.replay.loader import ReplayData
from osbench.replay.server import build_app


@pytest.fixture
def replay_dir(tmp_path):
    gen = generate_run("tiny", 0, tmp_path / "gen")
    world = load_world_from_dir(gen)
    orders = pd.read_parquet(gen / "orders.parquet")
    out = gen / "nearest"
    SimEngine(world, orders, 0, "nearest", inventory_detail=True).run(out)
    return out


@pytest.mark.fast
def test_meta_and_day(replay_dir):
    data = ReplayData(replay_dir)
    m = data.meta()
    assert m["has_inventory_detail"] is True
    assert m["n_sku"] > 0 and m["day_max"] >= m["day_min"]
    day = data.orders_on_day(m["day_min"] + 1)
    assert "orders" in day and len(day["wh_stock"]) == len(m["wh_x"])


@pytest.mark.fast
def test_order_detail_has_lines_and_warehouse(replay_dir):
    data = ReplayData(replay_dir)
    # Find a day with orders and pick one.
    oid = None
    for d in range(1, data.meta()["day_max"]):
        got = data.orders_on_day(d)["orders"]
        if got:
            oid = got[0]["order_id"]
            break
    assert oid is not None
    o = data.order_detail(oid)
    assert o["found"]
    assert len(o["lines"]) >= 1
    assert "sku" in o["lines"][0] and "wh" in o["lines"][0]
    events = [s["event"] for s in o["stages"]]
    assert "order_created" in events
    # assigned_line is internal and excluded from the chronology.
    assert "assigned_line" not in events
    # Reconciled view: a summary + per-warehouse parcels, and lines carry a real fate.
    s = o["summary"]
    assert s["ordered"]["lines"] == len(o["lines"])
    # ordered units == delivered + in_transit + pending + lost (nothing vanishes).
    acc = sum(s[k]["units"] for k in ("delivered", "in_transit", "pending", "lost"))
    assert acc == s["ordered"]["units"]
    assert all(l["status"] in ("delivered", "in_transit", "pending", "lost") for l in o["lines"])
    assert "parcels" in o and all("status" in p for p in o["parcels"])


@pytest.mark.fast
def test_orders_on_day_outcome_and_cost(replay_dir):
    data = ReplayData(replay_dir)
    valid = {"delivered", "late", "partial", "pending", "lost"}
    seen = set()
    cost_range_ok = False
    for day in range(data.meta()["day_max"] + 1):
        r = data.orders_on_day(day)
        assert "cost_range" in r and r["cost_range"][0] <= r["cost_range"][1]
        for o in r["orders"]:
            assert o["status"] in valid
            assert isinstance(o["cost"], (int, float)) and o["cost"] >= 0
            assert 0 <= o["delivered_lines"] <= o["n_lines"]
            # partial means strictly-incomplete delivery.
            if o["status"] == "partial":
                assert 0 < o["delivered_lines"] < o["n_lines"]
            # Lateness magnitude: a value only for fully delivered orders; delivered/late imply it.
            assert "lateness_days" in o
            if o["status"] in ("delivered", "late"):
                assert o["lateness_days"] is not None
            if o["status"] == "late":
                assert o["lateness_days"] > 0
            if o["status"] in ("pending",):
                assert o["lateness_days"] is None
            seen.add(o["status"])
        assert "lateness_range" in r and r["lateness_range"][0] <= r["lateness_range"][1]
        if r["orders"]:
            cost_range_ok = True
    assert cost_range_ok and seen  # at least one outcome observed


@pytest.mark.fast
def test_daily_series_lateness_distribution(replay_dir):
    from osbench.metrics.timeseries import daily_series

    a = daily_series(replay_dir)
    L = a["lateness"]
    assert len(L["labels"]) == len(L["counts"])
    assert L["n"] == sum(L["counts"])  # every delivered order falls in exactly one bucket
    assert L["late_orders"] <= L["n"]
    # on-time bucket is first; late orders are the rest.
    assert L["late_orders"] == L["n"] - L["counts"][0]


@pytest.mark.fast
def test_warehouse_summary_reconciles_backlog(replay_dir):
    data = ReplayData(replay_dir)
    w = data.warehouse_detail(0, day=5)
    s = w["summary"]
    # backlog = assigned lines that never shipped by the horizon (>= 0, and exactly the gap).
    assert s["backlog_lines"] == s["assigned_lines"] - s["shipped_lines"]
    assert s["backlog_lines"] >= 0
    assert len(w["assigned_day"]) == len(w["assigned"])


@pytest.mark.fast
def test_warehouse_and_sku_detail(replay_dir):
    data = ReplayData(replay_dir)
    w = data.warehouse_detail(0, day=5)
    assert len(w["stock"]) > 0 and "top_sku" in w
    sku = w["top_sku"][0]["sku"]
    s = data.sku_detail(sku)
    assert s["sku"] == sku
    assert len(s["stock_series"]) >= 1  # per-warehouse stock series


@pytest.mark.fast
def test_server_endpoints(replay_dir):
    client = TestClient(build_app(ReplayData(replay_dir)))
    assert client.get("/").status_code == 200
    assert client.get("/replay.js").status_code == 200
    m = client.get("/api/meta").json()
    assert client.get(f"/api/day/{m['day_min']}").status_code == 200
    assert client.get("/api/order/999999999").json()["found"] is False
    assert client.get("/api/warehouse/0?day=1").status_code == 200
    assert client.get("/api/sku/0").status_code == 200


@pytest.mark.fast
def test_inspector_without_inventory_detail(tmp_path):
    # A run without inventory detail still serves orders; per-SKU series are empty.
    gen = generate_run("tiny", 0, tmp_path / "gen")
    world = load_world_from_dir(gen)
    orders = pd.read_parquet(gen / "orders.parquet")
    out = gen / "nearest"
    SimEngine(world, orders, 0, "nearest", inventory_detail=False).run(out)
    data = ReplayData(out)
    assert data.meta()["has_inventory_detail"] is False
    w = data.warehouse_detail(0, day=1)
    assert w["stock"] == []  # no per-SKU stock without detail


@pytest.mark.fast
def test_catalog_views_paginate_and_filter(replay_dir):
    c = TestClient(build_app(ReplayData(replay_dir)))
    p = c.get("/api/products?day=3&limit=5").json()
    assert p["stock_source"] == "snapshot" and len(p["rows"]) == 5 and p["total"] > 5
    r = p["rows"][0]
    assert {"price", "unit_cost", "atp", "atp_wh", "ordered_units"} <= set(r)
    page2 = c.get("/api/products?day=3&limit=5&offset=5").json()["rows"]
    assert page2[0]["sku"] != r["sku"]
    d = c.get(f"/api/product/{r['sku']}").json()
    assert d["found"] and "total" in d["costs"] and d["demand"]["units"] == r["ordered_units"]
    w = c.get("/api/warehouses?day=3").json()
    assert w["total"] == len(w["rows"]) > 0 and w["rows"][0]["spark"]
    o = c.get("/api/orders?day_from=2&day_to=4&limit=10").json()
    assert o["total"] > 0 and all(2 <= x["day"] <= 4 for x in o["rows"])
    lost = c.get("/api/orders?status=lost").json()
    assert all(x["status"] == "lost" for x in lost["rows"])


@pytest.mark.fast
def test_stock_reconstructed_without_inventory_detail(replay_dir):
    # Large runs skip inventory_sku_daily: the journal reconstruction must match the snapshot.
    from osbench.replay.explore import Explorer

    data = ReplayData(replay_dir)
    snap = Explorer(data).stock_at(5)
    data.inv_sku = None
    rec = Explorer(data).stock_at(5)
    assert snap["source"] == "snapshot" and rec["source"] == "journal"
    assert (snap["book"] == rec["book"]).all()
    detail = Explorer(data).product_detail(0)
    assert detail["stock_source"] == "journal"


@pytest.mark.fast
def test_sales_ledger_reconciles(replay_dir):
    from osbench.replay.explore import Explorer

    c = TestClient(build_app(ReplayData(replay_dir)))
    s = c.get("/api/sales").json()
    k = s["kpi"]
    assert k["revenue"] > 0 and k["spend"] == pytest.approx(
        sum(k[x] for x in ("purchase", "fulfilment", "holding", "late_penalty", "lost_penalty")), abs=0.05)
    # Category allocations add up to the journal totals (holding / fulfilment are estimated per line).
    for key in ("revenue", "purchase", "fulfilment", "holding"):
        assert sum(r[key] for r in s["by_category"]) == pytest.approx(k[key], rel=1e-3, abs=0.05)
    assert sum(r["revenue"] for r in s["by_wh"]) == pytest.approx(k["revenue"], rel=1e-6)
    part = c.get("/api/sales?day_from=2&day_to=4").json()
    assert part["from"] == 2 and part["to"] == 4 and part["kpi"]["revenue"] < k["revenue"]
    skus = c.get("/api/sales/skus?sort=margin&desc=false&limit=3").json()
    assert len(skus["rows"]) == 3 and skus["rows"][0]["margin"] <= skus["rows"][1]["margin"]
    # Without stock snapshots, the reconstructed holding still matches the journal.
    data = ReplayData(replay_dir)
    data.inv_sku = None
    s2 = Explorer(data).sales()
    assert sum(r["holding"] for r in s2["by_category"]) == pytest.approx(s2["kpi"]["holding"], rel=1e-3)
