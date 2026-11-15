"""Everything the strategy page needs to know about a selection, fetched once.

Per plot (static, stored in `plot_site`): topsoil texture and organic carbon
(SoilGrids), available water (Saxton & Rawls 2006), erodibility (RUSLE K,
Williams 1995 EPIC), slope and LS factor (Copernicus 30 m DEM, McCool).
Per selection (from caches that already exist): country, 30-year ERA5
normals with frost dates and reference evapotranspiration, the previous
season of each plot from its NDVI periods, GAEZ suitability and yield, and
selling prices. A part that fails is None and listed in `errors`.
"""

import calendar
import logging
import math
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import asdict, dataclass
from datetime import date, timedelta

import numpy as np
from rasterio.features import geometry_mask
from rasterio.io import MemoryFile
from rasterio.transform import from_bounds
from shapely.geometry import Polygon, mapping

from app.db import ensure_table, pool
from app.geo import Rect
from app.services import gaez, soilgrids
from app.services.country import country_at
from app.services.drought import et0_series
from app.services.field_ndvi import (
    Params,
    analyse,
    fetch_observations,
    plot_cache_key,
    plot_polygon,
    shrink_polygon,
)
from app.services.prices import pipeline as prices
from app.services.sentinel_hub import SentinelHub
from app.services.weather import era5_daily
from app.timing import emit, timed

log = logging.getLogger("app.site")

VERSION = 1
CLIMATE_YEARS = (1995, 2024)
HISTORY_YEARS = 3
NDVI_BUFFER_M = 15.0
FROST_PROBABILITY = 0.8
DEM_RESOLUTION_M = 30
DEM_MARGIN_DEG = 0.0006
DEM_MAX_PX = 512
SLOPE_LENGTH_M = 100.0
PRICE_REFERENCE_YEARS = 5
WORKERS = 6
# parts still loading after this are left out and keep warming the caches
CONTEXT_DEADLINE_S = 20.0
DB_TIMEOUT = 5
DB_RETRY_S = 60

DEM_EVALSCRIPT = """//VERSION=3
function setup() { return { input: ["DEM"], output: { bands: 1, sampleType: "FLOAT32" } }; }
function evaluatePixel(s) { return [s.DEM]; }"""

DDL = """
CREATE TABLE IF NOT EXISTS plot_site (
    plot_key     text PRIMARY KEY,
    version      integer NOT NULL,
    sand_pct     double precision,
    clay_pct     double precision,
    soc_g_kg     double precision,
    awc_mm_per_m double precision,
    k_factor     double precision,
    slope_pct    double precision,
    ls_factor    double precision,
    fetched_at   timestamptz NOT NULL DEFAULT now()
)"""

PLOT_COLUMNS = ("sand_pct", "clay_pct", "soc_g_kg", "awc_mm_per_m", "k_factor", "slope_pct", "ls_factor")


@dataclass(frozen=True)
class PlotSite:
    sand_pct: float | None
    clay_pct: float | None
    soc_g_kg: float | None
    awc_mm_per_m: float | None
    k_factor: float | None
    slope_pct: float | None
    ls_factor: float | None


def awc_saxton_rawls(sand_pct: float, clay_pct: float, soc_g_kg: float) -> float:
    """Plant-available water (field capacity minus wilting point), mm per metre of soil."""
    s, c = sand_pct / 100, clay_pct / 100
    om = soc_g_kg / 10 * 1.724
    t1500 = -0.024 * s + 0.487 * c + 0.006 * om + 0.005 * s * om - 0.013 * c * om + 0.068 * s * c + 0.031
    wilting = t1500 + (0.14 * t1500 - 0.02)
    t33 = -0.251 * s + 0.195 * c + 0.011 * om + 0.006 * s * om - 0.027 * c * om + 0.452 * s * c + 0.299
    field_capacity = t33 + (1.283 * t33**2 - 0.374 * t33 - 0.015)
    return round((field_capacity - wilting) * 1000, 1)


def k_factor_epic(sand_pct: float, clay_pct: float, soc_g_kg: float) -> float:
    """RUSLE soil erodibility in t·ha·h/(ha·MJ·mm), Williams (1995) EPIC equation."""
    silt = max(0.0, 100 - sand_pct - clay_pct)
    oc = soc_g_kg / 10
    sn = 1 - sand_pct / 100
    f_sand = 0.2 + 0.3 * math.exp(-0.256 * sand_pct * (1 - silt / 100))
    f_clay_silt = (silt / (clay_pct + silt)) ** 0.3 if clay_pct + silt > 0 else 0.0
    f_orgc = 1 - 0.25 * oc / (oc + math.exp(3.72 - 2.95 * oc))
    f_hisand = 1 - 0.7 * sn / (sn + math.exp(-5.51 + 22.9 * sn))
    return round(f_sand * f_clay_silt * f_orgc * f_hisand * 0.1317, 4)


def ls_mccool(slope_fraction: np.ndarray, length_m: float = SLOPE_LENGTH_M) -> np.ndarray:
    theta = np.arctan(slope_fraction)
    sin = np.sin(theta)
    s_factor = np.where(slope_fraction < 0.09, 10.8 * sin + 0.03, 16.8 * sin - 0.50)
    beta = (sin / 0.0896) / (3 * np.power(sin, 0.8) + 0.56)
    m = beta / (1 + beta)
    return np.power(length_m / 22.13, m) * s_factor


def slope_stats(dem: np.ndarray, bounds: tuple[float, float, float, float], poly: Polygon) -> tuple[float, float]:
    """Mean slope % and mean LS factor of the DEM pixels inside the plot."""
    west, south, east, north = bounds
    height, width = dem.shape
    lat = (north + south) / 2
    dx = (east - west) / width * 111_320 * math.cos(math.radians(lat))
    dy = (north - south) / height * 110_540
    gy, gx = np.gradient(dem.astype(float), dy, dx)
    slope = np.hypot(gx, gy)
    inside = geometry_mask([mapping(poly)], out_shape=dem.shape,
                           transform=from_bounds(west, south, east, north, width, height), invert=True)
    if not inside.any():
        inside = np.ones_like(inside)
    return round(float(slope[inside].mean() * 100), 2), round(float(ls_mccool(slope[inside]).mean()), 3)


def frost_dates(dates: list[date], tmin: list[float | None], probability: float = FROST_PROBABILITY) -> dict | None:
    """Spring frost later than `last_spring` and autumn frost earlier than `first_autumn`
    each happen in at most 1 - probability of years (daily minimum below 0 °C)."""
    spring: dict[int, int] = {}
    autumn: dict[int, int] = {}
    for d, t in zip(dates, tmin):
        if t is None or t >= 0:
            continue
        doy = d.timetuple().tm_yday
        if d.month <= 6:
            spring[d.year] = max(spring.get(d.year, 0), doy)
        else:
            autumn[d.year] = min(autumn.get(d.year, 400), doy)
    if not spring or not autumn:
        return None
    last_spring = int(np.quantile(list(spring.values()), probability))
    first_autumn = int(np.quantile(list(autumn.values()), 1 - probability))
    as_md = lambda doy: (date(2001, 1, 1) + timedelta(days=doy - 1)).strftime("%m-%d")  # noqa: E731
    return {"last_spring": as_md(last_spring), "first_autumn": as_md(first_autumn)}


def monthly_normals(dates: list[date], tmean: list, precip: list, et0: list) -> list[dict]:
    totals: dict[tuple[int, int], dict[str, list[float]]] = {}
    for d, t, p, e in zip(dates, tmean, precip, et0):
        cell = totals.setdefault((d.year, d.month), {"t": [], "p": [], "e": []})
        for key, v in (("t", t), ("p", p), ("e", e)):
            if v is not None:
                cell[key].append(v)
    out = []
    for month in range(1, 13):
        cells = [v for (y, m), v in totals.items() if m == month]
        complete = [c for (y, m), c in totals.items() if m == month and len(c["p"]) == calendar.monthrange(y, m)[1]]
        mean = lambda xs: round(float(np.mean(xs)), 1) if xs else None  # noqa: E731
        out.append({
            "month": month,
            "tmean_c": mean([np.mean(c["t"]) for c in cells if c["t"]]),
            "precip_mm": mean([sum(c["p"]) for c in complete]),
            "et0_mm": mean([sum(c["e"]) for c in complete if len(c["e"]) == len(c["p"])]),
        })
    return out


def previous_season(periods: list[dict], today: date) -> dict:
    """The most recent finished crop on the plot: winter crop, summer crop, or fallow."""
    finished = [p for p in periods if p["type"] == "crop" and date.fromisoformat(p["end"]) < today]
    if not finished:
        return {"type": "unknown", "end": None, "confidence": None}
    last = finished[-1]
    end = date.fromisoformat(last["end"])
    if (today - end).days > 365:
        return {"type": "fallow", "end": last["end"], "confidence": last["confidence"]}
    kind = {"winter": "winter_crop", "winter_or_spring": "winter_crop", "summer": "summer_crop"}.get(
        last["crop_type"] or "", "unknown")
    return {"type": kind, "end": last["end"], "confidence": last["confidence"]}


_db_down_until = 0.0
_memory: dict[str, PlotSite] = {}
_inflight: dict[str, Future] = {}
_inflight_lock = threading.Lock()


def _db_ok() -> bool:
    if time.monotonic() < _db_down_until:
        return False
    ensure_table("plot_site", DDL)
    return True


def _db_failed(what: str, e: Exception) -> None:
    global _db_down_until
    _db_down_until = time.monotonic() + DB_RETRY_S
    log.warning("site %s failed, skipping the DB for %ss: %s", what, DB_RETRY_S, e)


def _read_plot(key: str) -> PlotSite | None:
    try:
        if not _db_ok():
            return None
        with pool.connection(timeout=DB_TIMEOUT) as conn:
            row = conn.execute(
                f"SELECT {', '.join(PLOT_COLUMNS)} FROM plot_site WHERE plot_key = %s AND version = %s",
                (key, VERSION),
            ).fetchone()
        return PlotSite(*row) if row else None
    except Exception as e:
        _db_failed("plot read", e)
        return None


def _write_plot(key: str, site: PlotSite) -> None:
    try:
        if not _db_ok():
            return
        cols = ", ".join(PLOT_COLUMNS)
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in PLOT_COLUMNS)
        with pool.connection(timeout=DB_TIMEOUT) as conn:
            conn.execute(
                f"""INSERT INTO plot_site (plot_key, version, {cols}) VALUES (%s, %s, {', '.join(['%s'] * len(PLOT_COLUMNS))})
                    ON CONFLICT (plot_key) DO UPDATE SET version = EXCLUDED.version, {updates}, fetched_at = now()""",
                (key, VERSION, *_site_values(site)),
            )
    except Exception as e:
        _db_failed("plot write", e)


def _site_values(site: PlotSite) -> tuple:
    return tuple(getattr(site, c) for c in PLOT_COLUMNS)


def _fetch_slope(sh: SentinelHub, poly: Polygon) -> tuple[float, float]:
    west, south, east, north = poly.bounds
    west, south, east, north = west - DEM_MARGIN_DEG, south - DEM_MARGIN_DEG, east + DEM_MARGIN_DEG, north + DEM_MARGIN_DEG
    lat = (north + south) / 2
    width = min(DEM_MAX_PX, max(8, math.ceil((east - west) * 111_320 * math.cos(math.radians(lat)) / DEM_RESOLUTION_M)))
    height = min(DEM_MAX_PX, max(8, math.ceil((north - south) * 110_540 / DEM_RESOLUTION_M)))
    tiff = sh.image([west, south, east, north], "2020-01-01", "2020-01-02", DEM_EVALSCRIPT,
                    width=width, height=height, collection="dem")
    with MemoryFile(tiff) as mem, mem.open() as src:
        dem = src.read(1)
    return slope_stats(dem, (west, south, east, north), poly)


def _fetch_plot(sh: SentinelHub, poly: Polygon, errors: list[str]) -> PlotSite:
    point = poly.representative_point()
    soil = None
    try:
        with timed("site soil"):
            soil = soilgrids.topsoil(point.y, point.x)
    except Exception as e:
        errors.append(f"soil: {e}")
    slope = ls = None
    try:
        with timed("site slope"):
            slope, ls = _fetch_slope(sh, poly)
    except Exception as e:
        errors.append(f"slope: {e}")
    if soil:
        sand, clay, soc = soil["sand"], soil["clay"], soil["soc"]
        awc, k = awc_saxton_rawls(sand, clay, soc), k_factor_epic(sand, clay, soc)
    else:
        sand = clay = soc = awc = k = None
    return PlotSite(sand, clay, soc, awc, k, slope, ls)


def plot_site(sh: SentinelHub, key: str, poly: Polygon) -> tuple[PlotSite, list[str]]:
    """Static inputs of one plot; fetched once, shared with requests already fetching it."""
    if key in _memory:
        return _memory[key], []
    with _inflight_lock:
        running = _inflight.get(key)
        if running is None:
            mine: Future = Future()
            _inflight[key] = mine
    if running is not None:
        return running.result(), []
    try:
        site = _read_plot(key)
        errors: list[str] = []
        if site is None:
            site = _fetch_plot(sh, poly, errors)
            if not errors:
                _write_plot(key, site)
        else:
            emit("site plot db hit", key=key)
        if not errors:
            _memory[key] = site
        mine.set_result(site)
        return site, errors
    except BaseException as e:
        mine.set_exception(e)
        raise
    finally:
        with _inflight_lock:
            _inflight.pop(key, None)


_shared_futures: dict[tuple, Future] = {}


def _shared(key: tuple, fn, *args):
    """Run fn once for concurrent callers with the same key."""
    with _inflight_lock:
        running = _shared_futures.get(key)
        if running is None:
            mine: Future = Future()
            _shared_futures[key] = mine
    if running is not None:
        return running.result()
    try:
        result = fn(*args)
        mine.set_result(result)
        return result
    except BaseException as e:
        mine.set_exception(e)
        raise
    finally:
        with _inflight_lock:
            _shared_futures.pop(key, None)


def _climate(rect: Rect) -> dict:
    start, end = date(CLIMATE_YEARS[0], 1, 1), date(CLIMATE_YEARS[1], 12, 31)
    series = era5_daily(rect, start, end)
    et0 = et0_series(series)
    v = series.values
    return {
        "years": list(CLIMATE_YEARS),
        "frost": frost_dates(series.dates, v["tmin"]),
        "months": monthly_normals(series.dates, v["tmean"], v["precip"], et0),
    }


def _history(sh: SentinelHub, plot_id: str | None, points: list[tuple[float, float]], today: date) -> dict:
    years = list(range(today.year - HISTORY_YEARS + 1, today.year + 1))
    inner = shrink_polygon(plot_polygon(points), NDVI_BUFFER_M)
    obs = fetch_observations(sh, inner, years, plot_cache_key(plot_id, points, NDVI_BUFFER_M))
    season = previous_season(analyse(obs, years, Params())["summary"], today)
    if plot_id is None and season["confidence"] == "high":
        season["confidence"] = "medium"
    return season


def _crop_id(name: str) -> str:
    return "".join(ch if ch.isalpha() else "_" for ch in name.lower()).strip("_")


def _gaez(polys: list[Polygon]) -> list[dict]:
    cells = [gaez.Cell.at(p.representative_point().y, p.representative_point().x) for p in polys]
    values = gaez.cells_values("high", set(cells))
    out = []
    for cell in cells:
        got = values[cell]
        if isinstance(got, gaez.GaezError) or not got:
            out.append({})
            continue
        out.append({
            _crop_id(c["crop"]): {
                "code": c["code"],
                "suitability": c["suitability"],
                "yield_kg_ha": c["attainable_yield_kg_ha"],
            }
            for c in gaez.crop_list("high", got, "not_suitable", None)
        })
    return out


def _prices(country: str) -> dict:
    data = prices.country_prices(country)
    by_crop: dict[str, list] = {}
    for row in data.rows:
        by_crop.setdefault(row.crop_id, []).append(row)
    out = {}
    for crop_id, rows in by_crop.items():
        s = prices.summarize(rows, PRICE_REFERENCE_YEARS, None)
        out[crop_id] = {
            "reference_eur_t": s["market_reference_price"],
            "variability_cv": s["variability"]["cv"] if s["variability"] else None,
            "confidence": s["confidence"],
        }
    return {"price_year": data.price_year, "crops": out}


_pool = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="site")


def site_context(sh: SentinelHub, outlines: list[tuple[str | None, list[tuple[float, float]]]]) -> dict:
    today = date.today()
    polys = [plot_polygon(points) for _, points in outlines]
    union_w, union_s, union_e, union_n = (
        min(p.bounds[0] for p in polys), min(p.bounds[1] for p in polys),
        max(p.bounds[2] for p in polys), max(p.bounds[3] for p in polys),
    )
    centre = ((union_s + union_n) / 2, (union_w + union_e) / 2)
    country = country_at(*centre)
    errors: list[str] = []

    def guarded(name: str, fn, *args):
        try:
            return fn(*args)
        except Exception as e:
            errors.append(f"{name}: {e}")
            return None

    deadline = time.monotonic() + CONTEXT_DEADLINE_S

    def collect(name: str, fut: Future):
        try:
            return fut.result(timeout=max(0.0, deadline - time.monotonic()))
        except FutureTimeout:
            errors.append(f"{name}: still loading after {CONTEXT_DEADLINE_S:.0f} s, left out")
            return None

    with timed("site context", logging.INFO, plots=len(outlines)):
        sites = [_pool.submit(plot_site, sh, plot_cache_key(pid, pts, 0), poly)
                 for (pid, pts), poly in zip(outlines, polys)]
        histories = [
            _pool.submit(guarded, "history", _shared, ("history", plot_cache_key(pid, pts, 0)), _history, sh, pid, pts, today)
            for pid, pts in outlines
        ]
        rect = Rect(union_n, union_w, union_s, union_e)
        climate = _pool.submit(guarded, "climate", _shared, ("climate", *rect.bbox), _climate, rect)
        suitability = _pool.submit(guarded, "gaez", _gaez, polys)
        price = _pool.submit(guarded, "prices", _prices, country) if country else None

        plots = []
        gaez_by_plot = collect("gaez", suitability) or [{} for _ in polys]
        for (pid, _), site_f, hist_f, gz in zip(outlines, sites, histories, gaez_by_plot):
            site_dict = None
            try:
                got = collect("plot", site_f)
                if got is not None:
                    site, site_errors = got
                    errors.extend(site_errors)
                    site_dict = asdict(site)
            except Exception as e:
                errors.append(f"plot: {e}")
            plots.append({"id": pid, "site": site_dict, "previous_season": collect("history", hist_f), "crops": gz})
        climate_value = collect("climate", climate)
        price_value = collect("prices", price) if price else None

    return {
        "country": country,
        "climate": climate_value,
        "prices": price_value,
        "plots": plots,
        "errors": list(errors),
    }


def prefetch(sh: SentinelHub, outlines: list[tuple[str | None, list[tuple[float, float]]]]) -> None:
    def warm() -> None:
        try:
            site_context(sh, outlines)
        except Exception as e:
            log.warning("site context prefetch failed: %s", e)

    emit("site context prefetch queued", level=logging.INFO, plots=len(outlines))
    threading.Thread(target=warm, name="site-prefetch", daemon=True).start()
