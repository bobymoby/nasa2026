"""Test 1: yearly counts of crop-critical hot and frost days."""

from dataclasses import dataclass
from datetime import date

from app.services.weather import DailySeries


@dataclass(frozen=True)
class EventIndex:
    key: str
    label: str
    variable: str  # "tmax" or "tmin"
    above: bool  # True: count days > threshold, False: days < threshold
    threshold: float
    start: tuple[int, int]  # (month, day), inclusive
    end: tuple[int, int]

    def describe(self) -> dict:
        op = ">" if self.above else "<"
        return {
            "label": self.label,
            "rule": f"{self.variable} {op} {self.threshold} °C",
            "window": f"{self.start[0]:02d}-{self.start[1]:02d}..{self.end[0]:02d}-{self.end[1]:02d}",
        }


def default_indices(july_heat_c=35.0, wheat_heat_c=32.0, frost_c=0.0) -> list[EventIndex]:
    return [
        EventIndex("july_heat", "Hot days in July (maize/sunflower flowering)",
                   "tmax", True, july_heat_c, (7, 1), (7, 31)),
        EventIndex("wheat_flowering_heat", "Hot days late May–mid June (wheat flowering)",
                   "tmax", True, wheat_heat_c, (5, 20), (6, 15)),
        EventIndex("april_frost", "Frost days in April (after spring crops sprout)",
                   "tmin", False, frost_c, (4, 1), (4, 30)),
    ]


def yearly_counts(
    series: DailySeries, indices: list[EventIndex], years: list[int]
) -> dict[str, list[int | None]]:
    """index key -> count per year (None when the window has no data)."""
    out: dict[str, list[int | None]] = {}
    for idx in indices:
        vals = series.values[idx.variable]
        counts = []
        for y in years:
            lo, hi = date(y, *idx.start), date(y, *idx.end)
            window = [v for d, v in zip(series.dates, vals) if lo <= d <= hi]
            present = [v for v in window if v is not None]
            if not present:
                counts.append(None)
                continue
            hits = (v > idx.threshold if idx.above else v < idx.threshold for v in present)
            counts.append(sum(hits))
        out[idx.key] = counts
    return out


def trend_per_decade(years: list[int], values: list[int | None]) -> float | None:
    """Ordinary least-squares slope, in days per decade."""
    pts = [(x, y) for x, y in zip(years, values) if y is not None]
    if len(pts) < 3:
        return None
    n = len(pts)
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    sxx = sum((p[0] - mx) ** 2 for p in pts)
    sxy = sum((p[0] - mx) * (p[1] - my) for p in pts)
    return round(10 * sxy / sxx, 2) if sxx else None


def top_years(years: list[int], values: list[int | None], n: int = 5) -> list[dict]:
    ranked = sorted(
        ((y, v) for y, v in zip(years, values) if v), key=lambda p: (-p[1], p[0])
    )
    return [{"year": y, "days": v} for y, v in ranked[:n]]
