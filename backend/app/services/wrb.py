"""Top WRB reference soil groups at a point, from ISRIC SoilGrids rasters (250 m).

Read from the cloud GeoTIFFs behind files.isric.org/soilgrids/latest/data/wrb/
(MostProbable.vrt plus one probability VRT per group), not the rate-limited
REST API. Postgres keeps, per 256 x 256 cell tile of the global grid, the three
most probable groups of every cell:

    soilgrids_wrb_tile  (row, col) -> classes, probabilities
                        3 bands x 65536 cells each, row-major uint8, 255 = no soil

A missing tile is read from ISRIC and stored. SoilGrids is static, so nothing
expires. Bulgaria is loaded up front with `load_area(*BULGARIA)`; the legend
(`group_shares`) only counts cells inside it, so it never waits on ISRIC.
If the database is unreachable, tiles are read from ISRIC and kept in memory.
"""

import logging
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np
import rasterio
from rasterio.windows import Window

from app.db import ensure_table, pool
from app.timing import timed

log = logging.getLogger("app.wrb")

BASE_URL = "https://files.isric.org/soilgrids/latest/data/wrb"
# grid of the WRB VRTs: 172800 x 67200 cells from 180W, 83.999167N
WEST = -180.0
NORTH = 83.99916720600001
CELL_DEG = 0.0020833330000008749
TILE = 256
TOP = 3
NODATA = 255
# west, south, east, north
BULGARIA = (22.3, 41.2, 28.7, 44.3)
LEGEND_MAX_SIDE = 512
READ_WORKERS = 16
DB_TIMEOUT = 5
DB_RETRY_S = 60

# pixel values of MostProbable, from its raster attribute table
WRB_GROUPS = [
    "Acrisols", "Albeluvisols", "Alisols", "Andosols", "Arenosols",
    "Calcisols", "Cambisols", "Chernozems", "Cryosols", "Durisols",
    "Ferralsols", "Fluvisols", "Gleysols", "Gypsisols", "Histosols",
    "Kastanozems", "Leptosols", "Lixisols", "Luvisols", "Nitisols",
    "Phaeozems", "Planosols", "Plinthosols", "Podzols", "Regosols",
    "Solonchaks", "Solonetz", "Stagnosols", "Umbrisols", "Vertisols",
]  # fmt: skip

DDL = """
    CREATE TABLE IF NOT EXISTS soilgrids_wrb_tile (
        row           integer NOT NULL,
        col           integer NOT NULL,
        classes       bytea NOT NULL,
        probabilities bytea NOT NULL,
        fetched_at    timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (row, col)
    )"""


class WrbError(RuntimeError):
    pass


@dataclass(frozen=True)
class TileTop:
    classes: np.ndarray  # (TOP, TILE, TILE)
    probabilities: np.ndarray


_memory: dict[tuple[int, int], TileTop] = {}
_tile_locks: dict[tuple[int, int], threading.Lock] = {}
_tile_locks_lock = threading.Lock()
_db_down_until = 0.0


def _db_failed(what: str, e: Exception) -> None:
    global _db_down_until
    _db_down_until = time.monotonic() + DB_RETRY_S
    log.warning("wrb %s failed, skipping the DB for %ss: %s", what, DB_RETRY_S, e)


def _read(name: str, window: Window) -> np.ndarray:
    env = rasterio.Env(
        GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
        GDAL_HTTP_MULTIRANGE="YES",
        GDAL_HTTP_MAX_RETRY="3",
        GDAL_HTTP_RETRY_DELAY="1",
    )
    try:
        with env, rasterio.open(f"/vsicurl/{BASE_URL}/{name}.vrt") as src:
            return src.read(1, window=window, boundless=True, fill_value=NODATA)
    except rasterio.errors.RasterioError as e:
        raise WrbError(f"SoilGrids read of {name} failed: {e}") from e


def _fetch(row0: int, col0: int, rows: int, cols: int) -> tuple[np.ndarray, np.ndarray]:
    """Top classes and probabilities over a window of tiles, each (TOP, h, w)."""
    window = Window(col0 * TILE, row0 * TILE, cols * TILE, rows * TILE)
    names = ["MostProbable", *WRB_GROUPS]
    with timed("wrb read", logging.INFO, tiles=rows * cols, layers=len(names)):
        with ThreadPoolExecutor(max_workers=READ_WORKERS) as ex:
            most, *probs = ex.map(lambda n: _read(n, window), names)
    stacked = np.stack(probs).astype(np.int16)
    stacked[stacked == NODATA] = -1
    order = np.argsort(-stacked, axis=0, kind="stable")[:TOP]
    top = np.take_along_axis(stacked, order, axis=0)
    # MostProbable is null over water and built-up land even where groups have a probability
    empty = (most == NODATA)[None] | (top <= 0)
    classes = np.where(empty, NODATA, order).astype(np.uint8)
    return classes, np.where(empty, NODATA, top).astype(np.uint8)


def _split(row0: int, col0: int, classes: np.ndarray, probabilities: np.ndarray) -> dict[tuple[int, int], TileTop]:
    tiles = {}
    for r in range(classes.shape[1] // TILE):
        for c in range(classes.shape[2] // TILE):
            block = np.s_[:, r * TILE:(r + 1) * TILE, c * TILE:(c + 1) * TILE]
            tiles[(row0 + r, col0 + c)] = TileTop(
                np.ascontiguousarray(classes[block]), np.ascontiguousarray(probabilities[block])
            )
    return tiles


def _store(tiles: dict[tuple[int, int], TileTop]) -> None:
    if time.monotonic() < _db_down_until:
        return
    try:
        ensure_table("soilgrids_wrb_tile", DDL)
        with pool.connection(timeout=DB_TIMEOUT) as conn:
            conn.cursor().executemany(
                """INSERT INTO soilgrids_wrb_tile (row, col, classes, probabilities) VALUES (%s, %s, %s, %s)
                   ON CONFLICT (row, col) DO UPDATE SET classes = EXCLUDED.classes,
                       probabilities = EXCLUDED.probabilities, fetched_at = now()""",
                [(r, c, t.classes.tobytes(), t.probabilities.tobytes()) for (r, c), t in tiles.items()],
            )
    except Exception as e:
        _db_failed("tile write", e)


def _load_stored(key: tuple[int, int]) -> TileTop | None:
    if time.monotonic() < _db_down_until:
        return None
    try:
        ensure_table("soilgrids_wrb_tile", DDL)
        with pool.connection(timeout=DB_TIMEOUT) as conn:
            row = conn.execute(
                "SELECT classes, probabilities FROM soilgrids_wrb_tile WHERE row = %s AND col = %s", key
            ).fetchone()
    except Exception as e:
        _db_failed("tile read", e)
        return None
    if row is None:
        return None
    shape = (TOP, TILE, TILE)
    return TileTop(*(np.frombuffer(b, dtype=np.uint8).reshape(shape) for b in row))


def _tile(key: tuple[int, int]) -> TileTop:
    if key in _memory:
        return _memory[key]
    with _tile_locks_lock:
        lock = _tile_locks.setdefault(key, threading.Lock())
    with lock:
        if key in _memory:
            return _memory[key]
        tile = _load_stored(key)
        if tile is None:
            tile = _split(*key, *_fetch(*key, 1, 1))[key]
            _store({key: tile})
        _memory[key] = tile
        return tile


def _cell(lat: float, lon: float) -> tuple[int, int]:
    return math.floor((NORTH - lat) / CELL_DEG), math.floor((lon - WEST) / CELL_DEG)


def top_groups(lat: float, lon: float, count: int = TOP) -> list[tuple[str, int]]:
    """Up to `count` (group, probability %) pairs, most probable first; empty over water and built-up land."""
    row, col = _cell(lat, lon)
    tile = _tile((row // TILE, col // TILE))
    r, c = row % TILE, col % TILE
    return [
        (WRB_GROUPS[int(cls)], int(p))
        for cls, p in zip(tile.classes[:count, r, c], tile.probabilities[:count, r, c])
        if cls != NODATA
    ]


def load_area(west: float, south: float, east: float, north: float) -> int:
    """Reads and stores every tile over a lon/lat box in one read per layer; returns the tile count."""
    top_row, left_col = (v // TILE for v in _cell(north, west))
    bottom_row, right_col = (v // TILE for v in _cell(south, east))
    rows, cols = bottom_row - top_row + 1, right_col - left_col + 1
    tiles = _split(top_row, left_col, *_fetch(top_row, left_col, rows, cols))
    _memory.update(tiles)
    _store(tiles)
    return len(tiles)


def group_shares(west: float, south: float, east: float, north: float) -> list[tuple[str, float]]:
    """Share of the soil cells in a box (clipped to Bulgaria) per most probable group, largest first."""
    west, south = max(west, BULGARIA[0]), max(south, BULGARIA[1])
    east, north = min(east, BULGARIA[2]), min(north, BULGARIA[3])
    if west >= east or south >= north:
        return []
    top_row, left_col = _cell(north, west)
    bottom_row, right_col = _cell(south, east)
    step = max(1, math.ceil(max(bottom_row - top_row, right_col - left_col) / LEGEND_MAX_SIDE))
    rows = np.arange(top_row, bottom_row + 1, step)
    cols = np.arange(left_col, right_col + 1, step)
    counts = np.zeros(NODATA + 1, dtype=np.int64)
    for tile_row in np.unique(rows // TILE):
        in_rows = rows[rows // TILE == tile_row] % TILE
        for tile_col in np.unique(cols // TILE):
            in_cols = cols[cols // TILE == tile_col] % TILE
            top = _tile((int(tile_row), int(tile_col))).classes[0]
            counts += np.bincount(top[np.ix_(in_rows, in_cols)].ravel(), minlength=NODATA + 1)
    soil = counts[: len(WRB_GROUPS)]
    total = soil.sum()
    if not total:
        return []
    return sorted(
        ((WRB_GROUPS[i], float(n / total)) for i, n in enumerate(soil) if n),
        key=lambda g: -g[1],
    )
