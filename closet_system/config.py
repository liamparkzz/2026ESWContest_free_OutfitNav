from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any
import yaml


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path).resolve()
    with config_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    ai_cfg = cfg.get("ai", {}) or {}
    inherit = ai_cfg.get("inherit_config")
    if inherit:
        inherit_path = Path(inherit)
        candidates = [inherit_path, Path.cwd() / inherit_path, config_path.parent / inherit_path]
        inherited_path = next((p.resolve() for p in candidates if p.exists()), None)
        if inherited_path:
            with inherited_path.open("r", encoding="utf-8") as f:
                inherited = yaml.safe_load(f) or {}
            inherited_ai = inherited.get("ai", {}) or {}
            cfg["ai"] = _deep_merge(inherited_ai, ai_cfg)

    cfg.setdefault("slot_count", 8)
    cfg.setdefault("database", {}).setdefault("path", "smart_closet.db")
    return cfg
