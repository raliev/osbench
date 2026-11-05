"""Tests for the world/demand generator and reproducibility (CRN)."""

from __future__ import annotations

import hashlib

import pandas as pd
import pytest

from osbench.generate import generate_run
from osbench.rng.streams import STREAM_NAMES, RngHub


def _orders_digest(run_dir) -> str:
    df = pd.read_parquet(run_dir / "orders.parquet")
    return hashlib.sha256(pd.util.hash_pandas_object(df, index=True).values.tobytes()).hexdigest()


@pytest.mark.fast
def test_same_seed_reproducible(tmp_path):
    a = generate_run("tiny", 0, tmp_path / "a")
    b = generate_run("tiny", 0, tmp_path / "b")
    assert _orders_digest(a) == _orders_digest(b)


@pytest.mark.fast
def test_different_seed_differs(tmp_path):
    a = generate_run("tiny", 0, tmp_path / "a")
    c = generate_run("tiny", 1, tmp_path / "c")
    assert _orders_digest(a) != _orders_digest(c)


@pytest.mark.fast
def test_orders_structure(tmp_path):
    run = generate_run("small", 0, tmp_path / "s")
    o = pd.read_parquet(run / "orders.parquet")
    assert set(o.columns) == {
        "order_id", "line_id", "region_id", "created_min",
        "dest_x", "dest_y", "sku_id", "qty",
    }
    assert (o.groupby("order_id").line_id.min() == 0).all()
    assert o.groupby("order_id").created_min.first().is_monotonic_increasing
    assert o.qty.min() >= 1
    # Mean number of lines per order is close to 1.6.
    mean_lines = len(o) / o.order_id.nunique()
    assert 1.4 < mean_lines < 1.8


@pytest.mark.fast
def test_rng_streams_independent():
    hub = RngHub(0)
    # Different streams produce different sequences.
    g1 = hub.generator("demand_arrivals").random(5)
    g2 = hub.generator("policy").random(5)
    assert not (g1 == g2).all()
    # Re-requesting the same stream from a fresh hub is reproducible.
    assert (RngHub(0).generator("geo").random(3) == RngHub(0).generator("geo").random(3)).all()


@pytest.mark.fast
def test_unknown_stream_raises():
    with pytest.raises(KeyError):
        RngHub(0).generator("nonexistent")
    assert "policy" in STREAM_NAMES
