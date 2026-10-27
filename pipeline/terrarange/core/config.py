from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_yaml_config(path: str | Path) -> dict[str, Any]:
    """Load a YAML config file into a dictionary."""
    with Path(path).open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def get_methodology_version(config: dict[str, Any]) -> str:
    """Return the methodology version for a config dictionary."""
    return str(config.get("methodology", {}).get("version") or config.get("methodology_version", "unknown"))
