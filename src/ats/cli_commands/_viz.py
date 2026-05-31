"""Shared terminal-viz primitives (sparklines, trend-colored series prints).

Used by `ats data` and `ats process` so there is one canonical implementation.
"""

from __future__ import annotations

from rich.console import Console

SPARK_BLOCKS = "▁▂▃▄▅▆▇█"


def sparkline(values: list[float]) -> str:
    """Map a numeric series to Unicode block characters (oldest→newest, left→right)."""
    if not values:
        return ""
    lo, hi = min(values), max(values)
    span = hi - lo
    if span == 0:
        return SPARK_BLOCKS[0] * len(values)
    out = []
    for v in values:
        idx = round((v - lo) / span * (len(SPARK_BLOCKS) - 1))
        out.append(SPARK_BLOCKS[idx])
    return "".join(out)


def print_series(
    console: Console, label: str, series: list[float], fmt: str = ".2f"
) -> None:
    """Print a labeled, trend-colored sparkline with first/last/min/max stats."""
    if not series:
        console.print(f"[yellow]No data for {label}.[/yellow]")
        return
    first, last = series[0], series[-1]
    rising = last >= first
    color = "green" if rising else "red"
    arrow = "▲" if rising else "▼"
    spark = sparkline(series)
    pct = ((last - first) / abs(first) * 100) if first != 0 else 0.0
    console.print(f"[bold]{label}[/bold]  ({len(series)} pts)")
    console.print(f"  [{color}]{spark}[/{color}]")
    console.print(
        f"  first {first:{fmt}}  last [{color}]{last:{fmt}}[/{color}]  "
        f"min {min(series):{fmt}}  max {max(series):{fmt}}  "
        f"[{color}]{arrow} {pct:+.2f}%[/{color}]\n"
    )
