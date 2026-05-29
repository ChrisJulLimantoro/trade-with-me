from __future__ import annotations

from pathlib import Path

import yaml

_SEEDS_DIR = Path(__file__).parents[3] / "seeds"


def load_m1_universe() -> list[str]:
    path = _SEEDS_DIR / "universe_m1.yaml"
    data = yaml.safe_load(path.read_text())
    return [str(s) for s in data["symbols"]]


def load_xvenue_mapping() -> dict[str, dict[str, str]]:
    path = _SEEDS_DIR / "xvenue_symbols.yaml"
    data: dict[str, dict[str, str]] = yaml.safe_load(path.read_text())
    return data
