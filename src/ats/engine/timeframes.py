"""Timeframe string → duration. Matches the M1 universe (15m / 1h / 4h)."""

from __future__ import annotations

import re
from datetime import timedelta

_UNIT = {"m": "minutes", "h": "hours", "d": "days"}


def timeframe_to_timedelta(tf: str) -> timedelta:
    m = re.fullmatch(r"(\d+)([mhd])", tf.strip())
    if not m:
        raise ValueError(f"unrecognized timeframe '{tf}' (use e.g. 15m, 1h, 4h)")
    return timedelta(**{_UNIT[m.group(2)]: int(m.group(1))})
