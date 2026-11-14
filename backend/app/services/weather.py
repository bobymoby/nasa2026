"""Daily weather from ERA5 (via the Open-Meteo archive API) and NASA POWER.

Both sources are gridded point services, so a rectangle is sampled on the
source's grid and the daily values are averaged across the sample points.
"""

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, timedelta

import httpx

from app.cache import cached_json
from app.geo import Rect

OPEN_METEO_URL = "https://archive-api.open-meteo.com/v1/archive"
POWER_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"

# our variable name -> source variable name
ERA5_VARS = {
    "tmax": "temperature_2m_max",  # °C
    "tmin": "temperature_2m_min",  # °C
    "precip": "precipitation_sum",  # mm
    "rh_max": "relative_humidity_2m_max",  # %
    "rh_min": "relative_humidity_2m_min",  # %
    "wind10": "wind_speed_10m_mean",  # m/s at 10 m
    "rs": "shortwave_radiation_sum",  # MJ/m²/day
}
POWER_VARS = {"tmax": "T2M_MAX", "tmin": "T2M_MIN"}
POWER_FIRST_YEAR = 1981

ERA5_GRID_DEG, ERA5_MAX_POINTS = 0.25, 4
POWER_GRID_DEG, POWER_MAX_POINTS = 0.5, 4


class WeatherError(RuntimeError):
    pass


@dataclass
class DailySeries:
    source: str
    dates: list[date]
    values: dict[str, list[float | None]]
    points: list[tuple[float, float]]  # grid points actually used (lat, lon)
    latitude: float  # mean latitude of the points
    elevation: float  # mean elevation of the points, m
    meta: dict = field(default_factory=dict)

    def rows(self) -> list[dict]:
        keys = list(self.values)
        return [
            {"date": d.isoformat(), **{k: self.values[k][i] for k in keys}}
            for i, d in enumerate(self.dates)
        ]


def _mean(vals: list[float | None]) -> float | None:
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 3) if vals else None


def _date_range(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


# ---------------------------------------------------------------- ERA5


def _fetch_open_meteo(points, start: date, end: date) -> list[dict]:
    params = {
        "latitude": ",".join(str(p[0]) for p in points),
        "longitude": ",".join(str(p[1]) for p in points),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "models": "era5",
        "daily": ",".join(ERA5_VARS.values()),
        "wind_speed_unit": "ms",
        "timezone": "GMT",
    }
    # long ranges count as many "calls" against Open-Meteo's per-minute limit,
    # so back-to-back 40-year requests can get a 429: wait for the window to reset
    for wait in (20, 40, 60, None):
        r = httpx.get(OPEN_METEO_URL, params=params, timeout=180)
        if r.status_code != 429 or wait is None:
            break
        time.sleep(wait)
    if not r.is_success:
        raise WeatherError(f"Open-Meteo ERA5 request failed ({r.status_code}): {r.text}")
    j = r.json()
    return j if isinstance(j, list) else [j]


def era5_daily(rect: Rect, start: date, end: date) -> DailySeries:
    points = rect.grid(ERA5_GRID_DEG, ERA5_MAX_POINTS)
    locs = cached_json(
        "era5",
        {"points": points, "start": str(start), "end": str(end), "vars": ERA5_VARS},
        lambda: _fetch_open_meteo(points, start, end),
    )
    dates = [date.fromisoformat(t) for t in locs[0]["daily"]["time"]]
    values = {
        ours: [
            _mean([loc["daily"][theirs][i] for loc in locs]) for i in range(len(dates))
        ]
        for ours, theirs in ERA5_VARS.items()
    }
    return DailySeries(
        source="era5",
        dates=dates,
        values=values,
        points=[(loc["latitude"], loc["longitude"]) for loc in locs],
        latitude=sum(p[0] for p in points) / len(points),
        elevation=sum(loc.get("elevation", 0) for loc in locs) / len(locs),
        meta={"provider": "Open-Meteo archive API, models=era5", "grid_deg": 0.25},
    )


# ---------------------------------------------------------------- NASA POWER


def _fetch_power_point(lat: float, lon: float, start: date, end: date) -> dict:
    params = {
        "parameters": ",".join(POWER_VARS.values()),
        "community": "AG",
        "latitude": lat,
        "longitude": lon,
        "start": start.strftime("%Y%m%d"),
        "end": end.strftime("%Y%m%d"),
        "format": "JSON",
    }
    r = httpx.get(POWER_URL, params=params, timeout=180)
    if not r.is_success:
        raise WeatherError(f"NASA POWER request failed ({r.status_code}): {r.text}")
    j = r.json()
    return {"parameter": j["properties"]["parameter"], "geometry": j["geometry"]}


def power_daily(rect: Rect, start: date, end: date) -> DailySeries:
    start = max(start, date(POWER_FIRST_YEAR, 1, 1))
    points = rect.grid(POWER_GRID_DEG, POWER_MAX_POINTS)

    def one(p):
        return cached_json(
            "power",
            {"point": p, "start": str(start), "end": str(end), "vars": POWER_VARS},
            lambda: _fetch_power_point(p[0], p[1], start, end),
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        locs = list(pool.map(one, points))

    dates = _date_range(start, end)
    keys = [d.strftime("%Y%m%d") for d in dates]

    def val(loc, var, k):
        v = loc["parameter"][var].get(k)
        return None if v is None or v <= -999 else v  # -999 = missing

    values = {
        ours: [_mean([val(loc, theirs, k) for loc in locs]) for k in keys]
        for ours, theirs in POWER_VARS.items()
    }
    return DailySeries(
        source="power",
        dates=dates,
        values=values,
        points=points,
        latitude=sum(p[0] for p in points) / len(points),
        elevation=sum(loc["geometry"]["coordinates"][2] for loc in locs) / len(locs),
        meta={"provider": "NASA POWER daily point API (MERRA-2 based)", "grid_deg": 0.5},
    )
