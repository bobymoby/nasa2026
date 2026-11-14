"""Field-scale spectral indicators calculated by the Sentinel Hub Statistical API."""

import math
import statistics
from collections import defaultdict
from datetime import date, datetime
from typing import Any

import numpy as np

from app.cache import cached_json
from app.services.sentinel_hub import SentinelHub, SentinelHubError

INDEX_NAMES = ("ndvi", "ndmi", "bsi", "savi", "ndre")
TREND_WINDOW_DAYS = 31
TREND_WINDOW_RADIUS_DAYS = TREND_WINDOW_DAYS // 2
TREND_MIN_YEARS = 3

EVAL_SOIL_STATS = """//VERSION=3
function setup() {
  return {
    input: [{ bands: ["B02", "B04", "B05", "B08", "B11", "SCL", "dataMask"] }],
    output: [
      { id: "ndvi", bands: 1, sampleType: "FLOAT32" },
      { id: "ndmi", bands: 1, sampleType: "FLOAT32" },
      { id: "bsi", bands: 1, sampleType: "FLOAT32" },
      { id: "savi", bands: 1, sampleType: "FLOAT32" },
      { id: "ndre", bands: 1, sampleType: "FLOAT32" },
      { id: "bare", bands: 1, sampleType: "FLOAT32" },
      { id: "dataMask", bands: 1 }
    ]
  };
}
function evaluatePixel(s) {
  const invalidScl = [0, 1, 2, 3, 6, 7, 8, 9, 10, 11];
  const invalid = s.dataMask === 0 || invalidScl.includes(Math.round(s.SCL));
  const ndviDen = s.B08 + s.B04;
  const ndmiDen = s.B08 + s.B11;
  const bsiDen = s.B11 + s.B04 + s.B08 + s.B02;
  const saviDen = s.B08 + s.B04 + 0.5;
  const ndreDen = s.B08 + s.B05;
  const denominators = [ndviDen, ndmiDen, bsiDen, saviDen, ndreDen];
  const reflectances = [s.B02, s.B04, s.B05, s.B08, s.B11];
  const badReflectance = reflectances.some(v => !Number.isFinite(v) || v <= 0);
  const badDenominator = denominators.some(v => !Number.isFinite(v) || Math.abs(v) < 1e-8);
  if (invalid || badReflectance || badDenominator) {
    return {
      ndvi: [NaN], ndmi: [NaN], bsi: [NaN], savi: [NaN], ndre: [NaN],
      bare: [NaN], dataMask: [0]
    };
  }
  return {
    ndvi: [(s.B08 - s.B04) / ndviDen],
    ndmi: [(s.B08 - s.B11) / ndmiDen],
    bsi: [(s.B11 + s.B04 - s.B08 - s.B02) / bsiDen],
    savi: [1.5 * (s.B08 - s.B04) / saviDen],
    ndre: [(s.B08 - s.B05) / ndreDen],
    bare: [Math.round(s.SCL) === 5 ? 1 : 0],
    dataMask: [1]
  };
}"""


def _date(value: str | date, name: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an ISO date (YYYY-MM-DD)") from exc


def _resolution(bbox: list[float]) -> float:
    west, south, east, north = bbox
    return max(0.0001, max(east - west, north - south) / 2000)


def _band_stats(item: dict, output: str) -> tuple[float | None, int, int]:
    try:
        stats = item["outputs"][output]["bands"]["B0"]["stats"]
        sample_count = int(stats.get("sampleCount", 0))
        no_data_count = int(stats.get("noDataCount", 0))
        mean = stats.get("mean")
    except (KeyError, TypeError, ValueError) as exc:
        raise SentinelHubError(
            f"Malformed Sentinel Hub Statistical API response for {output}"
        ) from exc
    if mean is not None:
        try:
            mean = float(mean)
        except (TypeError, ValueError) as exc:
            raise SentinelHubError(
                f"Malformed Sentinel Hub Statistical API mean for {output}"
            ) from exc
        if not math.isfinite(mean):
            mean = None
    valid_count = max(0, sample_count - no_data_count)
    return mean, valid_count, sample_count


def _trend(
    observations: list[dict[str, Any]], field: str
) -> tuple[str, dict[str, Any]]:
    eligible = [
        (index, date.fromisoformat(item["date"]).timetuple().tm_yday)
        for index, item in enumerate(observations)
        if item.get(field) is not None
    ]
    if not eligible:
        return "insufficient_data", {
            "years_used": [],
            "observations_used": 0,
            "window_center_day_of_year": None,
        }

    best: list[int] = []
    best_center: int | None = None
    for _, center in eligible:
        indices = [
            index for index, doy in eligible
            if min(abs(doy - center), 366 - abs(doy - center))
            <= TREND_WINDOW_RADIUS_DAYS
        ]
        years = {observations[index]["date"][:4] for index in indices}
        if len(years) > len({observations[index]["date"][:4] for index in best}):
            best = indices
            best_center = center

    grouped: dict[int, list[float]] = defaultdict(list)
    for index in best:
        grouped[int(observations[index]["date"][:4])].append(
            float(observations[index][field])
        )
    points = sorted((year, statistics.fmean(values)) for year, values in grouped.items())
    basis = {
        "years_used": [year for year, _ in points],
        "observations_used": sum(len(values) for values in grouped.values()),
        "window_center_day_of_year": best_center,
    }
    if len(points) < TREND_MIN_YEARS:
        return "insufficient_data", basis

    slope = float(np.polyfit(
        [year for year, _ in points],
        [value for _, value in points],
        1,
    )[0])
    if abs(slope) < 1e-12:
        return "stable", basis
    return ("increasing" if slope > 0 else "decreasing"), basis


def _calculate_soil_characteristics(
    sh: SentinelHub,
    bbox: list[float],
    start: date,
    end: date,
) -> dict:
    raw = sh.stats(
        bbox,
        start.isoformat(),
        end.isoformat(),
        EVAL_SOIL_STATS,
        interval="P1D",
        res_deg=_resolution(bbox),
        collection="sentinel-2-l2a",
        max_cloud=100,
        end_exclusive=True,
    )

    daily: dict[str, dict[str, Any]] = {}
    observations_found = 0
    observations_used = 0
    total_valid_pixels = 0
    total_bare_pixels = 0.0
    for item in raw.get("data", []):
        if "error" in item:
            continue
        try:
            acquired_date = date.fromisoformat(item["interval"]["from"][:10]).isoformat()
        except (KeyError, TypeError, ValueError) as exc:
            raise SentinelHubError(
                "Malformed Sentinel Hub Statistical API interval"
            ) from exc

        _, valid_count, sample_count = _band_stats(item, "ndvi")
        if sample_count == 0:
            continue
        observations_found += 1
        if valid_count == 0:
            continue

        metrics = {}
        for name in INDEX_NAMES:
            value, _, _ = _band_stats(item, name)
            metrics[name] = round(value, 4) if value is not None else None
        bare_fraction, _, _ = _band_stats(item, "bare")
        metrics["bare_soil_percent"] = (
            round(bare_fraction * 100, 4) if bare_fraction is not None else None
        )
        metrics["date"] = acquired_date
        daily[acquired_date] = metrics
        observations_used += 1
        total_valid_pixels += valid_count
        if bare_fraction is not None:
            total_bare_pixels += bare_fraction * valid_count

    time_series = [
        {key: value for key, value in row.items() if key != "bare_soil_percent"}
        for _, row in sorted(daily.items())
    ]

    def overall_mean(name: str) -> float | None:
        values = [row[name] for row in time_series if row[name] is not None]
        return round(statistics.fmean(values), 4) if values else None

    bare_soil_percent = (
        round(100 * total_bare_pixels / total_valid_pixels, 1)
        if total_valid_pixels else None
    )
    bare_soil_series = [
        {"date": row["date"], "bare_soil_percent": row["bare_soil_percent"]}
        for _, row in sorted(daily.items())
    ]
    trend_inputs = {
        "ndvi": _trend(time_series, "ndvi"),
        "ndmi": _trend(time_series, "ndmi"),
        "bare_soil_percent": _trend(bare_soil_series, "bare_soil_percent"),
    }
    trend_basis = {
        "method": "linear_regression",
        "calendar_window_days": TREND_WINDOW_DAYS,
        "minimum_years": TREND_MIN_YEARS,
        "requested_date_range": {"from": start.isoformat(), "to": end.isoformat()},
        "available_observation_dates": {
            "from": time_series[0]["date"] if time_series else None,
            "to": time_series[-1]["date"] if time_series else None,
        },
        "indicators": {
            f"{name}_trend": result[1] for name, result in trend_inputs.items()
        },
    }
    return {
        "vegetation_condition": {
            "ndvi": overall_mean("ndvi"),
            "savi": overall_mean("savi"),
        },
        "water_stress": {"ndmi": overall_mean("ndmi")},
        "soil_exposure": {
            "bsi": overall_mean("bsi"),
            "bare_soil_percent": bare_soil_percent,
        },
        "chlorophyll_indicator": {"ndre": overall_mean("ndre")},
        "historical_changes": {
            "ndvi_trend": trend_inputs["ndvi"][0],
            "ndmi_trend": trend_inputs["ndmi"][0],
            "bare_soil_trend": trend_inputs["bare_soil_percent"][0],
        },
        "time_series": time_series,
        "valid_observations_percent": (
            round(100 * observations_used / observations_found, 1)
            if observations_found else 0.0
        ),
        "observations_found": observations_found,
        "observations_used": observations_used,
        "observation_unit": "daily interval with Sentinel-2 data",
        "trend_basis": trend_basis,
    }


def get_soil_characteristics(
    sh: SentinelHub,
    bbox: list[float],
    date_from: str | date,
    date_to: str | date,
) -> dict:
    """Calculate field indicators remotely and cache completed historical ranges."""
    start, end = _date(date_from, "date_from"), _date(date_to, "date_to")
    if start > end:
        raise ValueError("date_from must be on or before date_to")
    if (
        len(bbox) != 4
        or not all(math.isfinite(float(value)) for value in bbox)
        or not (-180 <= bbox[0] < bbox[2] <= 180)
        or not (-90 <= bbox[1] < bbox[3] <= 90)
    ):
        raise ValueError("bbox must be [west, south, east, north] in EPSG:4326")

    calculate = lambda: _calculate_soil_characteristics(sh, bbox, start, end)
    if end < date.today():
        return cached_json(
            "soil_characteristics",
            {
                "version": 4,
                "bbox": bbox,
                "date_from": start.isoformat(),
                "date_to": end.isoformat(),
            },
            calculate,
        )
    return calculate()
