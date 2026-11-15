"""SoilGrids property layers for Bulgaria, stored in Postgres.

The REST API in `soilgrids.py` is slow and often unavailable, so the mean of
every property and depth it serves is read once from ISRIC's rasters
(files.isric.org/soilgrids/latest/data/<property>/<property>_<depth>_mean.vrt,
Interrupted Goode Homolosine, 250 m) and kept per 128 x 128 cell tile:

    soilgrids_property_tile  (row, col, layer) -> vals
                             vals: zlib of int16 128 x 128, row-major, -32768 = no data

Values are raw SoilGrids integers; divide by the layer's d_factor as with the
REST API. Load with `load_bulgaria()`; it stores each layer as soon as it is
read and skips layers already stored, so a failed run can simply be repeated.
Points without every layer stored return None so callers fall back to the REST API.
"""

import logging
import math
import time
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.windows import Window

from app.db import ensure_table, pool
from app.timing import timed

log = logging.getLogger("app.soilgrids_store")

BASE_URL = "https://files.isric.org/soilgrids/latest/data"
HOMOLOSINE = "+proj=igh +lat_0=0 +lon_0=0 +datum=WGS84 +units=m +no_defs"
# grid of the property VRTs
ORIGIN_X = -19949750.0
ORIGIN_Y = 8361000.0
CELL_M = 250.0
TILE = 128
NODATA = -32768
# west, south, east, north
BULGARIA = (22.3, 41.2, 28.7, 44.3)
READ_WORKERS = 8
DB_TIMEOUT = 5
DB_RETRY_S = 60

# property -> d_factor, as the REST API reports them
D_FACTORS = {
    "phh2o": 10,
    "nitrogen": 100,
    "soc": 10,
    "clay": 10,
    "sand": 10,
    "silt": 10,
    "bdod": 100,
}
DEPTHS = ("0-5cm", "5-15cm", "15-30cm", "30-60cm", "60-100cm", "100-200cm")
LAYERS = [f"{prop}_{depth}" for prop in D_FACTORS for depth in DEPTHS]

DDL = """
    CREATE TABLE IF NOT EXISTS soilgrids_property_tile (
        row        integer NOT NULL,
        col        integer NOT NULL,
        layer      text NOT NULL,
        vals       bytea NOT NULL,
        fetched_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (row, col, layer)
    )"""

_to_homolosine = Transformer.from_crs("EPSG:4326", HOMOLOSINE, always_xy=True)
_db_down_until = 0.0


def _cell(lat: float, lon: float) -> tuple[int, int]:
    x, y = _to_homolosine.transform(lon, lat)
    return math.floor((ORIGIN_Y - y) / CELL_M), math.floor((x - ORIGIN_X) / CELL_M)


def _in_bulgaria(lat: float, lon: float) -> bool:
    west, south, east, north = BULGARIA
    return west <= lon <= east and south <= lat <= north


@lru_cache(maxsize=32)
def _tile(row: int, col: int) -> dict[str, np.ndarray]:
    ensure_table("soilgrids_property_tile", DDL)
    with pool.connection(timeout=DB_TIMEOUT) as conn:
        found = conn.execute(
            "SELECT layer, vals FROM soilgrids_property_tile WHERE row = %s AND col = %s", (row, col)
        ).fetchall()
    return {
        layer: np.frombuffer(zlib.decompress(data), dtype=np.int16).reshape(TILE, TILE)
        for layer, data in found
    }


def stored_layers(lat: float, lon: float, properties: tuple[str, ...]) -> list[dict] | None:
    """The point's layers in the REST API's `properties.layers` shape, or None if not stored."""
    global _db_down_until
    if not _in_bulgaria(lat, lon) or time.monotonic() < _db_down_until:
        return None
    row, col = _cell(lat, lon)
    try:
        tile = _tile(row // TILE, col // TILE)
    except Exception as e:
        _db_down_until = time.monotonic() + DB_RETRY_S
        log.warning("soilgrids tile read failed, skipping the DB for %ss: %s", DB_RETRY_S, e)
        return None
    if any(f"{prop}_{depth}" not in tile for prop in properties for depth in DEPTHS):
        return None
    r, c = row % TILE, col % TILE
    out = []
    for prop in properties:
        depths = []
        for depth in DEPTHS:
            value = int(tile[f"{prop}_{depth}"][r, c])
            depths.append({"label": depth, "values": {"mean": None if value == NODATA else value}})
        out.append({"name": prop, "unit_measure": {"d_factor": D_FACTORS[prop]}, "depths": depths})
    return out


def _read(layer: str, window: Window) -> np.ndarray:
    prop = layer.split("_", 1)[0]
    env = rasterio.Env(
        GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
        GDAL_HTTP_MULTIRANGE="YES",
        GDAL_HTTP_MAX_RETRY="5",
        GDAL_HTTP_RETRY_DELAY="2",
    )
    with timed("soilgrids layer read", logging.INFO, layer=layer):
        with env, rasterio.open(f"/vsicurl/{BASE_URL}/{prop}/{layer}_mean.vrt") as src:
            return src.read(1, window=window, boundless=True, fill_value=NODATA)


def load_bulgaria() -> list[str]:
    """Reads every layer not yet stored over Bulgaria from ISRIC and stores its tiles; returns the layers that failed."""
    west, south, east, north = BULGARIA
    edges = [_cell(lat, lon) for lat in np.linspace(south, north, 50) for lon in (west, east)]
    edges += [_cell(lat, lon) for lat in (south, north) for lon in np.linspace(west, east, 50)]
    top, left = min(r for r, _ in edges) // TILE, min(c for _, c in edges) // TILE
    bottom, right = max(r for r, _ in edges) // TILE, max(c for _, c in edges) // TILE
    window = Window(left * TILE, top * TILE, (right - left + 1) * TILE, (bottom - top + 1) * TILE)

    ensure_table("soilgrids_property_tile", DDL)
    with pool.connection(timeout=DB_TIMEOUT) as conn:
        done = {layer for (layer,) in conn.execute("SELECT DISTINCT layer FROM soilgrids_property_tile")}
    todo = [layer for layer in LAYERS if layer not in done]
    log.info("soilgrids: %d of %d layers stored, reading %d", len(done & set(LAYERS)), len(LAYERS), len(todo))

    def load(layer: str) -> None:
        grid = _read(layer, window)
        tiles = []
        for r in range(bottom - top + 1):
            for c in range(right - left + 1):
                block = np.ascontiguousarray(grid[r * TILE:(r + 1) * TILE, c * TILE:(c + 1) * TILE])
                if (block != NODATA).any():
                    tiles.append((top + r, left + c, layer, zlib.compress(block.tobytes(), 6)))
        with pool.connection(timeout=DB_TIMEOUT) as conn:
            conn.cursor().executemany(
                """INSERT INTO soilgrids_property_tile (row, col, layer, vals) VALUES (%s, %s, %s, %s)
                   ON CONFLICT (row, col, layer) DO UPDATE SET vals = EXCLUDED.vals, fetched_at = now()""",
                tiles,
            )

    failed = []
    with ThreadPoolExecutor(max_workers=READ_WORKERS) as ex:
        futures = {ex.submit(load, layer): layer for layer in todo}
        for finished, future in enumerate(as_completed(futures), 1):
            layer = futures[future]
            try:
                future.result()
                log.info("soilgrids: stored %s (%d/%d)", layer, finished, len(todo))
            except Exception as e:
                failed.append(layer)
                log.warning("soilgrids: %s failed (%d/%d): %s", layer, finished, len(todo), e)
    _tile.cache_clear()
    return failed
