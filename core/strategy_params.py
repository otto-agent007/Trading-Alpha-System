"""Centralized strategy parameters.

All tunable trading thresholds live here.  At startup, ``_load()`` reads
``DATA_PATH/params.json`` and overrides any of the defaults below.  If the
file doesn't exist, the system runs with the original hardcoded defaults —
no behavioral change.

Drop in a new params.json and restart to apply optimized parameters.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from config import DATA_PATH

logger = logging.getLogger(__name__)

PARAMS_FILE = DATA_PATH / "params.json"

_DEFAULTS: dict = {
    "min_edge": 0.03,
    "max_edge": 0.35,
    "min_confidence": 0.40,
    "min_volume_usd": 500,
    "max_days_to_close": 90,
    "kelly_fraction": 0.25,
    "max_position_pct": 0.05,
    "bt_min_sample": 8,
    "bt_min_ev": 0.02,
    "bt_min_win_rate": 0.50,
    "bt_max_drawdown": 0.40,
    "price_floor": 0.03,
    "price_ceiling": 0.97,
    "suspicious_edge": 0.30,
    "confidence_cap_on_suspicious": 0.45,
}


@dataclass
class StrategyParams:
    min_edge: float
    max_edge: float
    min_confidence: float
    min_volume_usd: int
    max_days_to_close: int
    kelly_fraction: float
    max_position_pct: float
    bt_min_sample: int
    bt_min_ev: float
    bt_min_win_rate: float
    bt_max_drawdown: float
    price_floor: float
    price_ceiling: float
    suspicious_edge: float
    confidence_cap_on_suspicious: float

    def __str__(self) -> str:
        lines = [f"  {k}: {v}" for k, v in self.__dict__.items()]
        return "StrategyParams(\n" + "\n".join(lines) + "\n)"


def _load() -> StrategyParams:
    data = dict(_DEFAULTS)
    if PARAMS_FILE.exists():
        try:
            overrides = json.loads(PARAMS_FILE.read_text(encoding="utf-8"))
            applied = {k: v for k, v in overrides.items() if k in _DEFAULTS}
            data.update(applied)
            logger.info(f"StrategyParams: loaded {PARAMS_FILE} ({len(applied)} keys applied)")
        except Exception as e:
            logger.warning(f"StrategyParams: failed to load {PARAMS_FILE} ({e}), using defaults")
    else:
        logger.debug("StrategyParams: no params.json found, using defaults")
    return StrategyParams(**data)


SP: StrategyParams = _load()
