"""Test 3: field-average Sentinel-2 NDVI per clear acquisition, plus yearly metrics."""

import statistics
from collections import defaultdict
from datetime import date

from app.cache import cached_json
from app.geo import Rect
from app.services.sentinel_hub import EVAL_NDVI_STATS, SentinelHub

S2_FIRST_YEAR = 2017  # Sentinel-2 L2A is complete over Europe from 2017


def _resolution(rect: Rect) -> float:
    """~10 m pixels, coarsened for big rectangles (Statistical API max 2500 px)."""
    span = max(rect.east - rect.west, rect.north - rect.south)
    return max(0.0001, span / 2000)


def field_observations(sh: SentinelHub, rect: Rect, year: int, min_clear: float) -> list[dict]:
    """One row per acquisition day with the field's mean NDVI and clear-pixel fraction."""

    def fetch():
        return sh.stats(
            rect.bbox,
            f"{year}-01-01",
            f"{year}-12-31",
            EVAL_NDVI_STATS,
            interval="P1D",
            res_deg=_resolution(rect),
        )

    complete = year < date.today().year
    raw = (
        cached_json("s2_ndvi", {"bbox": rect.bbox, "year": year}, fetch)
        if complete
        else fetch()
    )
    rows = []
    for item in raw.get("data", []):
        if "error" in item:
            continue
        st = item["outputs"]["ndvi"]["bands"]["B0"]["stats"]
        total = st.get("sampleCount", 0)
        valid = total - st.get("noDataCount", 0)
        if total == 0 or valid == 0:
            continue
        clear = valid / total
        if clear < min_clear or st.get("mean") is None:
            continue
        rows.append({
            "date": item["interval"]["from"][:10],
            "ndvi": round(st["mean"], 4),
            "clear_fraction": round(clear, 3),
        })
    return rows


def year_metrics(obs: list[dict], bare_threshold: float) -> dict:
    if not obs:
        return {"observations": 0}
    by_month: dict[int, list[float]] = defaultdict(list)
    for o in obs:
        by_month[int(o["date"][5:7])].append(o["ndvi"])
    monthly = {m: round(statistics.fmean(v), 3) for m, v in sorted(by_month.items())}

    # peak on a 3-observation running median, so one bad pixel day can't set it
    vals = [o["ndvi"] for o in obs]
    smooth = [statistics.median(vals[max(0, i - 1): i + 2]) for i in range(len(vals))]
    i_peak = max(range(len(smooth)), key=smooth.__getitem__)
    peak_month = int(obs[i_peak]["date"][5:7])

    if 4 <= peak_month <= 6:
        crop_type = "winter crop (spring peak)"
    elif 7 <= peak_month <= 9:
        crop_type = "summer crop (mid-summer peak)"
    else:
        crop_type = "unclear"

    return {
        "observations": len(obs),
        "monthly_mean": monthly,
        "peak_value": round(smooth[i_peak], 3),
        "peak_date": obs[i_peak]["date"],
        "peak_month": peak_month,
        "crop_type_guess": crop_type,
        "bare_months": [m for m, v in monthly.items() if v < bare_threshold],
        "months_without_clear_image": [m for m in range(1, 13) if m not in monthly],
    }
