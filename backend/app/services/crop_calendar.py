"""Planting and maturity dates per 0.5° cell for Bulgaria, stored in Postgres.

Source: GGCMI Phase 3 crop calendar v1.01 (Jägermeyr et al. 2021,
doi:10.5281/zenodo.5062513, CC BY 4.0), rainfed systems. In Europe it is
mostly built on SAGE (Sacks et al. 2010) and MIRCA2000. One NetCDF file per
crop on a global 0.5° grid, days of year 1-365:

    crop_calendar_cell  (crop, lat, lon) -> planting_doy, maturity_doy, data_source
                        crop: GGCMI code; lat, lon: cell centre;
                        data_source: GGCMI's index of the dataset behind the cell

Only cells from observed calendars are used. MIRCA2000 cells (data_source 1)
are monthly national calendars that GGCMI uses where nothing better exists; in
Bulgaria they give every crop one country-wide window, wrong for some (winter
rapeseed and field pea sown in May).

Load with `load_bulgaria()`; it stores each crop as soon as it is read and
skips crops already stored, so a failed run can simply be repeated. When crops
are missing, `dates_at` starts that load in the background (or, with the
database down, keeps Zenodo's data in memory) and returns what it has; crops
without usable dates fall back to the caller's own calendar.
"""

import logging
import math
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
import numpy as np
import rasterio

from app.db import ensure_table, pool
from app.timing import timed

log = logging.getLogger("app.crop_calendar")

SOURCE = "GGCMI Phase 3 crop calendar (Jägermeyr et al. 2021)"
URL = "https://zenodo.org/api/records/5062513/files/{code}_rf_ggcmi_crop_calendar_phase3_v1.01.nc4/content"
CELL_DEG = 0.5
# west, south, east, north
BULGARIA = (22.0, 41.0, 29.0, 44.5)
# Zenodo throttles parallel downloads
FETCH_WORKERS = 2
MIRCA = 1
# squared degrees: the plot's own cell, else an adjacent one (edge cells have no data)
MAX_DISTANCE_SQ = 0.5
FETCH_TIMEOUT_S = 60
FETCH_ATTEMPTS = 3
DB_TIMEOUT = 5
RETRY_S = 300

# app crop id -> GGCMI code; the other crops of crop_agronomy.csv have no GGCMI calendar
GGCMI_CODES = {
    "wheat": "wwh",
    "barley": "bar",
    "rye": "rye",
    "maize": "mai",
    "sorghum": "sor",
    "foxtail_millet": "mil",
    "sunflower": "sun",
    "rapeseed": "rap",
    "soybean": "soy",
    "dry_pea": "pea",
    "phaseolus_bean": "bea",
    "sugarbeet": "sgb",
    "white_potato": "pot",
    "cotton": "cot",
}
CROP_IDS = {code: crop_id for crop_id, code in GGCMI_CODES.items()}

DDL = """
    CREATE TABLE IF NOT EXISTS crop_calendar_cell (
        crop         text NOT NULL,
        lat          double precision NOT NULL,
        lon          double precision NOT NULL,
        planting_doy smallint NOT NULL,
        maturity_doy smallint NOT NULL,
        data_source  smallint NOT NULL,
        fetched_at   timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (crop, lat, lon)
    )"""

Row = tuple[str, float, float, int, int, int]
Dates = dict[str, tuple[int, int]]

_memory: dict[tuple[float, float], Dates] = {}
_fetched: list[Row] = []
_loading = threading.Lock()
_next_load = 0.0


def _cell(lat: float, lon: float) -> tuple[float, float]:
    return (
        math.floor(lat / CELL_DEG) * CELL_DEG + CELL_DEG / 2,
        math.floor(lon / CELL_DEG) * CELL_DEG + CELL_DEG / 2,
    )


def _in_bulgaria(lat: float, lon: float) -> bool:
    west, south, east, north = BULGARIA
    return west <= lon <= east and south <= lat <= north


def _download(code: str) -> bytes:
    for attempt in range(1, FETCH_ATTEMPTS + 1):
        try:
            response = httpx.get(URL.format(code=code), timeout=FETCH_TIMEOUT_S, follow_redirects=True)
            response.raise_for_status()
            return response.content
        except httpx.HTTPError:
            if attempt == FETCH_ATTEMPTS:
                raise
            time.sleep(5 * attempt)
    raise AssertionError("unreachable")


def _read_crop(code: str) -> list[Row]:
    with timed("crop calendar read", logging.INFO, crop=code):
        content = _download(code)
        grids = {}
        # the netCDF driver needs a real file
        with tempfile.NamedTemporaryFile(suffix=".nc4") as f:
            f.write(content)
            f.flush()
            for var in ("planting_day", "maturity_day", "data_source_used"):
                with rasterio.open(f'netcdf:"{f.name}":{var}') as src:
                    window = src.window(*BULGARIA).round_offsets().round_lengths()
                    grids[var] = (src.read(1, window=window), src.window_transform(window), src.nodata)
    planting, transform, nodata = grids["planting_day"]
    maturity = grids["maturity_day"][0]
    source = grids["data_source_used"][0]
    rows = []
    for r in range(planting.shape[0]):
        for c in range(planting.shape[1]):
            p, m, src = float(planting[r, c]), float(maturity[r, c]), float(source[r, c])
            if not np.isfinite([p, m, src]).all() or nodata in (p, m, src):
                continue
            if not (1 <= p <= 366 and 1 <= m <= 366):
                continue
            lon, lat = transform * (c + 0.5, r + 0.5)
            rows.append((code, round(lat, 2), round(lon, 2), round(p), round(m), round(src)))
    return rows


def _store(rows: list[Row]) -> None:
    ensure_table("crop_calendar_cell", DDL)
    with pool.connection(timeout=DB_TIMEOUT) as conn:
        conn.cursor().executemany(
            """INSERT INTO crop_calendar_cell (crop, lat, lon, planting_doy, maturity_doy, data_source)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (crop, lat, lon) DO UPDATE SET planting_doy = EXCLUDED.planting_doy,
                   maturity_doy = EXCLUDED.maturity_doy, data_source = EXCLUDED.data_source,
                   fetched_at = now()""",
            rows,
        )


def _stored_codes() -> set[str]:
    ensure_table("crop_calendar_cell", DDL)
    with pool.connection(timeout=DB_TIMEOUT) as conn:
        return {code for (code,) in conn.execute("SELECT DISTINCT crop FROM crop_calendar_cell")}


def _read_all(codes: list[str], keep) -> list[str]:
    failed = []
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as ex:
        futures = {ex.submit(_read_crop, code): code for code in codes}
        for finished, future in enumerate(as_completed(futures), 1):
            code = futures[future]
            try:
                keep(future.result())
                log.info("crop calendar: stored %s (%d/%d)", code, finished, len(codes))
            except Exception as e:
                failed.append(code)
                log.warning("crop calendar: %s failed (%d/%d): %s", code, finished, len(codes), e)
    return failed


def load_bulgaria() -> list[str]:
    """Reads every GGCMI crop not yet stored over Bulgaria from Zenodo and stores it; returns the crops that failed."""
    todo = [code for code in GGCMI_CODES.values() if code not in _stored_codes()]
    log.info("crop calendar: %d of %d crops stored, reading %d", len(GGCMI_CODES) - len(todo), len(GGCMI_CODES), len(todo))
    failed = _read_all(todo, _store)
    _memory.clear()
    return failed


def _load_into_memory() -> None:
    have = {row[0] for row in _fetched}
    _read_all([code for code in GGCMI_CODES.values() if code not in have], _fetched.extend)
    _memory.clear()


def _load_in_background(db_up: bool) -> None:
    global _next_load
    if time.monotonic() < _next_load or not _loading.acquire(blocking=False):
        return
    _next_load = time.monotonic() + RETRY_S

    def run() -> None:
        try:
            load_bulgaria() if db_up else _load_into_memory()
        except Exception as e:
            log.warning("crop calendar load failed: %s", e)
        finally:
            _loading.release()

    threading.Thread(target=run, name="crop-calendar-load", daemon=True).start()


def _nearest(rows, lat: float, lon: float) -> tuple[Dates, set[str]]:
    """Usable dates per crop id, and the GGCMI codes that have a cell here at all."""
    best: dict[str, tuple[float, int, int, int]] = {}
    for code, rlat, rlon, p, m, src in rows:
        d = (rlat - lat) ** 2 + (rlon - lon) ** 2
        if code in CROP_IDS and d <= MAX_DISTANCE_SQ and (code not in best or d < best[code][0]):
            best[code] = (d, p, m, src)
    usable = {CROP_IDS[code]: (p, m) for code, (_, p, m, src) in best.items() if src != MIRCA}
    return usable, set(best)


def _from_db(lat: float, lon: float) -> tuple[Dates, set[str]]:
    ensure_table("crop_calendar_cell", DDL)
    with pool.connection(timeout=DB_TIMEOUT) as conn:
        rows = conn.execute(
            """SELECT crop, lat, lon, planting_doy, maturity_doy, data_source FROM crop_calendar_cell
               WHERE abs(lat - %s) <= 1 AND abs(lon - %s) <= 1""",
            (lat, lon),
        ).fetchall()
    return _nearest(rows, lat, lon)


def dates_at(lat: float, lon: float) -> Dates:
    """Crop id -> (planting day of year, maturity day of year) from observed calendars; empty outside Bulgaria."""
    if not _in_bulgaria(lat, lon):
        return {}
    cell = _cell(lat, lon)
    if cell in _memory:
        return _memory[cell]
    db_up = True
    try:
        found, present = _from_db(*cell)
    except Exception as e:
        log.warning("crop calendar DB read failed: %s", e)
        db_up, found, present = False, {}, set()
    in_memory, in_memory_present = _nearest(_fetched, *cell)
    found, present = {**in_memory, **found}, present | in_memory_present
    if present == set(GGCMI_CODES.values()):
        _memory[cell] = found
    else:
        _load_in_background(db_up)
    return found
