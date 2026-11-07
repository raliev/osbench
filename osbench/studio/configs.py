"""Config IO for the studio: list / read / validate / save / clone scenario configs.

Presets bundled with the package are read-only; user configs live as YAML files in a
configs directory (default ``scenarios/``) and can be created, edited and run.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from ..config.loader import available_presets, load_scenario
from ..config.schema import ScenarioConfig


def list_configs(configs_dir: Path) -> dict:
    presets = sorted(available_presets())
    user = sorted(p.stem for p in Path(configs_dir).glob("*.yaml"))
    return {"presets": presets, "user": user}


def read_config(name: str, configs_dir: Path) -> dict:
    """Return {name, source, yaml, config} for a preset or user config."""
    user_path = Path(configs_dir) / f"{name}.yaml"
    if user_path.exists():
        text = user_path.read_text(encoding="utf-8")
        source = "user"
    elif name in available_presets():
        from importlib import resources

        text = resources.files("osbench.config.presets").joinpath(f"{name}.yaml").read_text("utf-8")
        source = "preset"
    else:
        raise FileNotFoundError(f"config not found: {name}")
    cfg = ScenarioConfig.model_validate(yaml.safe_load(text))
    return {"name": name, "source": source, "yaml": text, "config": cfg.model_dump(mode="json")}


def validate_config(config_json: dict) -> dict:
    """Validate a config dict; return {ok, errors, config}."""
    try:
        cfg = ScenarioConfig.model_validate(config_json)
        return {"ok": True, "errors": [], "config": cfg.model_dump(mode="json")}
    except ValidationError as e:
        errors = [
            {"loc": ".".join(str(x) for x in err["loc"]), "msg": err["msg"]}
            for err in e.errors()
        ]
        return {"ok": False, "errors": errors, "config": None}


# The advanced YAML popup edits only these two config blocks (2.12 stock-sync, 2.13 transfers).
_ADVANCED_KEYS = ("inventory_sync", "transfers")

_ADVANCED_EXAMPLE = """\
# Advanced settings — edit as YAML. Only `inventory_sync` and `transfers` are allowed here.
# A block you leave out (or fully comment) is left UNCHANGED; it is never reset.
# Uncomment and edit the blocks below, then press Validate, then Apply.

# ----- Stock-sync latency: the OMS sees WMS stock through a delayed snapshot -----
# inventory_sync:
#   mode: fixed_lag          # none | fixed_lag | periodic
#   lag_minutes: 60          # fixed_lag: delay before a WMS pick/receipt reaches the OMS
#   period_minutes: 15       # periodic: snapshot refresh interval
#   jitter_minutes: 0        # random extra delay per event
#   lagged_events: all       # picks | receipts | all
#   per_warehouse: {0: 0, 1: 60}   # optional per-warehouse lag/period override

# ----- Inter-warehouse transfers: move stock between warehouses ahead of demand -----
# transfers:
#   enabled: true
#   topology: all_pairs      # all_pairs | hub (rdc->fdc) | explicit
#   policy: threshold_balance   # none | threshold_balance | pull_base_stock
#   review_days: 1
#   protect_safety: true
#   visible_to_atp: true
#   on_outage: hold          # hold | redirect
#   params: {low_days: 5, high_days: 15, target_days: 10}   # threshold knobs (use {S: 10} for pull_base_stock)
#   guards: {min_qty: 1, max_source_share: 0.5, cooldown_days: 7, max_transfer_share: 1.0}
#   lanes:                   # optional explicit lanes / per-lane overrides
#     - {from_wh: 0, to_wh: 1, lead_time_days: 2, cost_per_unit: 0.5, capacity_units_per_day: 500}
"""


def advanced_yaml(config_json: dict) -> dict:
    """Build the text shown in the advanced popup: any block that is already non-default is
    dumped as live (uncommented) YAML to edit, followed by the commented example template."""
    from ..config.schema import InventorySyncConfig, TransfersConfig

    defaults = {
        "inventory_sync": InventorySyncConfig().model_dump(mode="json"),
        "transfers": TransfersConfig().model_dump(mode="json"),
    }
    active: dict = {}
    for key in _ADVANCED_KEYS:
        cur = config_json.get(key)
        if cur is not None and cur != defaults[key]:
            active[key] = cur
    head = ""
    if active:
        head = ("# Current advanced settings (edit below). Delete a block to leave it unchanged.\n"
                + yaml.safe_dump(active, sort_keys=False, allow_unicode=True) + "\n")
    return {"yaml": head + _ADVANCED_EXAMPLE}


def validate_advanced_yaml(text: str) -> dict:
    """Validate the advanced-settings YAML (only ``inventory_sync`` / ``transfers``).

    Returns {ok, error, parsed}. ``parsed`` maps each present block to its normalized dict,
    ready to merge into the scenario config. An empty / all-commented document is valid and
    yields no changes.
    """
    from ..config.schema import InventorySyncConfig, TransfersConfig

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        return {"ok": False, "error": f"YAML parse error: {e}", "parsed": {}}
    if data is None:
        return {"ok": True, "error": None, "parsed": {}}
    if not isinstance(data, dict):
        return {"ok": False, "error": "Top level must be a mapping of blocks, e.g. 'transfers:'.",
                "parsed": {}}
    unknown = [k for k in data if k not in _ADVANCED_KEYS]
    if unknown:
        return {"ok": False, "parsed": {},
                "error": f"Only {' / '.join(_ADVANCED_KEYS)} are allowed here; got: {', '.join(unknown)}."}
    models = {"inventory_sync": InventorySyncConfig, "transfers": TransfersConfig}
    parsed: dict = {}
    try:
        for key, model in models.items():
            if key in data and data[key] is not None:
                parsed[key] = model.model_validate(data[key]).model_dump(mode="json")
    except ValidationError as e:
        errs = "; ".join(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors())
        return {"ok": False, "error": errs, "parsed": {}}
    return {"ok": True, "error": None, "parsed": parsed}


def save_config(name: str, config_json: dict, configs_dir: Path) -> dict:
    """Validate and write a user config YAML. Returns {ok, path|errors}."""
    res = validate_config({**config_json, "name": name})
    if not res["ok"]:
        return {"ok": False, "errors": res["errors"]}
    Path(configs_dir).mkdir(parents=True, exist_ok=True)
    path = Path(configs_dir) / f"{name}.yaml"
    header = (
        f"# {name} — user config edited in osbench studio.\n"
        f"# (inline comments are not preserved; edit in the studio or add your own here)\n"
    )
    body = yaml.safe_dump(res["config"], sort_keys=False, allow_unicode=True, default_flow_style=False)
    path.write_text(header + body, encoding="utf-8")
    return {"ok": True, "path": str(path)}


def clone_config(src: str, dst: str, configs_dir: Path) -> dict:
    """Copy src (preset or user) to a new user config named dst."""
    data = read_config(src, configs_dir)
    cfg = data["config"]
    cfg["name"] = dst
    return save_config(dst, cfg, configs_dir)


def resolve_for_run(name: str, configs_dir: Path) -> str:
    """Return a scenario reference usable by `osbench run` (user YAML path or preset name)."""
    user_path = Path(configs_dir) / f"{name}.yaml"
    if user_path.exists():
        return str(user_path)
    if name in available_presets():
        return name
    # Fall back to generic loader resolution (raises if unknown).
    load_scenario(name)
    return name
