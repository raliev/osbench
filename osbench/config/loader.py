"""Loading and validation of YAML scenarios + deterministic config hash."""

from __future__ import annotations

import hashlib
import json
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from .schema import ScenarioConfig

def available_presets() -> set[str]:
    """Names (file stems) of all bundled preset YAMLs under config/presets/."""
    names = set()
    for entry in resources.files("osbench.config.presets").iterdir():
        if entry.name.endswith((".yaml", ".yml")):
            names.add(entry.name.rsplit(".", 1)[0])
    return names


def _resolve_source(ref: str | Path) -> str:
    """Return the YAML text by preset name or file path."""
    ref_str = str(ref)
    if ref_str in available_presets():
        with resources.files("osbench.config.presets").joinpath(f"{ref_str}.yaml").open(
            "r", encoding="utf-8"
        ) as fh:
            return fh.read()
    path = Path(ref_str)
    if not path.exists():
        raise FileNotFoundError(f"scenario not found: {ref_str} (neither a preset nor a path)")
    return path.read_text(encoding="utf-8")


def load_scenario(ref: str | Path) -> ScenarioConfig:
    """Load a scenario from a preset (tiny/small/...) or a YAML file."""
    raw = yaml.safe_load(_resolve_source(ref))
    return ScenarioConfig.model_validate(raw)


def config_hash(cfg: ScenarioConfig) -> str:
    """SHA256 of the canonical JSON dump of the config (stable regardless of key order)."""
    payload = _canonical(cfg.model_dump(mode="json"))
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _canonical(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _canonical(obj[k]) for k in sorted(obj)}
    if isinstance(obj, (list, tuple)):
        return [_canonical(x) for x in obj]
    return obj
