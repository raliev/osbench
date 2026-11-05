"""Expand a base scenario over a grid of axes into named scenario variants.

Used for the sensitivity grid (e.g. load x inventory-cover). Each axis is a dotted path
into the scenario config (e.g. ``initial_cover_days`` or ``demand.base_orders_per_day``)
mapped to a list of values; the Cartesian product yields one ScenarioConfig per cell.
"""

from __future__ import annotations

import itertools
from copy import deepcopy

from ..config.loader import load_scenario
from ..config.schema import ScenarioConfig


def _set_path(d: dict, path: str, value) -> None:
    keys = path.split(".")
    node = d
    for k in keys[:-1]:
        node = node[k]
    node[keys[-1]] = value


def _short(path: str) -> str:
    return path.split(".")[-1]


def expand_grid(base_ref: str, axes: dict[str, list]) -> list[tuple[str, ScenarioConfig, dict]]:
    """Return [(name, ScenarioConfig, axis_values)] for every cell of the grid."""
    base = load_scenario(base_ref).model_dump(mode="json")
    names = list(axes.keys())
    out = []
    for combo in itertools.product(*(axes[n] for n in names)):
        d = deepcopy(base)
        tag_parts = [base["name"]]
        axis_values = {}
        for path, val in zip(names, combo):
            _set_path(d, path, val)
            tag_parts.append(f"{_short(path)}{val}")
            axis_values[path] = val
        name = "__".join(tag_parts)
        d["name"] = name
        out.append((name, ScenarioConfig.model_validate(d), axis_values))
    return out
