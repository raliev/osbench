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
