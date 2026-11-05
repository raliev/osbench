"""Tests for configs and presets."""

from __future__ import annotations

import pytest

from osbench.config.loader import config_hash, load_scenario
from osbench.config.policy import WeightedScoreConfig
from osbench.config.schema import ScenarioConfig


@pytest.mark.fast
@pytest.mark.parametrize("preset", ["tiny", "small"])
def test_presets_load(preset):
    cfg = load_scenario(preset)
    assert isinstance(cfg, ScenarioConfig)
    assert cfg.name == preset
    assert cfg.n_warehouses >= 2
    assert cfg.sku.n_sku > 0


@pytest.mark.fast
def test_config_hash_stable():
    a = config_hash(load_scenario("small"))
    b = config_hash(load_scenario("small"))
    assert a == b
    assert a != config_hash(load_scenario("tiny"))


@pytest.mark.fast
def test_extra_fields_forbidden():
    with pytest.raises(Exception):
        ScenarioConfig.model_validate({"name": "x", "bogus_field": 1})


@pytest.mark.fast
def test_weighted_score_weights_sum_to_one():
    WeightedScoreConfig()  # default weights are valid
    with pytest.raises(Exception):
        WeightedScoreConfig(weights={"distance": 0.5})  # sum != 1


@pytest.mark.fast
def test_plane_mode_rejects_large_network():
    cfg = load_scenario("small").model_dump()
    cfg["warehouses"] = cfg["warehouses"] * 3  # 12 warehouses in plane mode
    with pytest.raises(Exception):
        ScenarioConfig.model_validate(cfg)
