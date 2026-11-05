"""All bundled presets (including demo configs) load and validate."""

from __future__ import annotations

import pytest

from osbench.config.loader import available_presets, load_scenario

_DEMOS = {
    "demo_contested",
    "demo_two_cities",
    "demo_big_basket",
    "demo_heavy",
    "demo_scarce",
    "demo_showcase",
    "demo_stranded",
}


@pytest.mark.fast
def test_all_presets_discovered():
    presets = available_presets()
    assert {"tiny", "small"} <= presets
    assert _DEMOS <= presets


@pytest.mark.fast
@pytest.mark.parametrize("name", sorted(_DEMOS))
def test_demo_preset_loads(name):
    cfg = load_scenario(name)
    assert cfg.name == name
    assert cfg.n_warehouses >= 1 and cfg.sku.n_sku > 0
    # Plane demos must stay within the plane-mode warehouse limit.
    if cfg.geo.mode == "plane":
        assert cfg.n_warehouses <= 8


@pytest.mark.fast
def test_big_basket_has_large_baskets():
    cfg = load_scenario("demo_big_basket")
    assert cfg.demand.lines_per_order_mean >= 3.0


@pytest.mark.fast
def test_scarce_is_tight():
    cfg = load_scenario("demo_scarce")
    assert cfg.initial_cover_days <= 10
