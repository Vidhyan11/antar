"""Config loading. Every knob in one YAML file, addressed by attribute."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "antar.yaml"


def _namespace(obj: Any) -> Any:
    if isinstance(obj, dict):
        return SimpleNamespace(**{k: _namespace(v) for k, v in obj.items()})
    if isinstance(obj, list):
        return [_namespace(v) for v in obj]
    return obj


def load_config(path: str | Path | None = None) -> SimpleNamespace:
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    with open(cfg_path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    ns = _namespace(raw)
    ns.__dict__["_source_path"] = str(cfg_path)
    ns.__dict__["_raw"] = raw
    return ns
