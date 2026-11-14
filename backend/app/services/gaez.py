"""Crop suitability from FAO GAEZ v4 (Global Agro-Ecological Zones).

GAEZ rates every 5 arc-minute cell (~9 km) of the world for ~50 crops from
climate, terrain and soil. We use, for the 1981-2010 climate and rainfed
farming, at high or low input level:

- suitability index, 0-10000, over all land in the cell (`sx{H|L}r_<crop>`)
- attainable yield of the best suitability class in the cell (`yx{H|L}r_<crop>`)

The values are read straight from FAO's cloud-optimised GeoTIFFs. Postgres
holds them in 1° tiles (12 x 12 cells, ~110 x 85 km in Bulgaria); a missing
tile triggers a read of its whole 10° region (one ranged read per layer,
~20 s), so one fetch covers all of Bulgaria and a plot never costs a request
of its own.

    gaez_crop        code -> name, group
    gaez_layer       GAEZ layer objectid -> crop, variable, input level, unit, GeoTIFF url
    gaez_tile        1° tile (row, col from 90N / 180W) -> tile_id
    gaez_tile_fetch  which input levels of a tile are stored, and when
    gaez_tile_value  (tile, layer) -> 144 cell values, row-major, -9 = no data

GAEZ is static, so nothing expires. If the database is unreachable, requests
still work: the DB is skipped for DB_RETRY_S after a failure and fetched tiles
are also kept in memory.
"""

import logging
import math
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass

import httpx
import numpy as np
import rasterio
from rasterio.windows import Window

from app.db import ensure_table, pool
from app.timing import emit, timed

log = logging.getLogger("app.gaez")

SERVICE = "https://gaez-services.fao.org/server/rest/services/res05/ImageServer"
TIFF_URL = "https://s3.eu-west-1.amazonaws.com/data.gaezdev.aws.fao.org/res05/CRUTS32/Hist/8110{level}/{name}.tif"
CELL_DEG = 1 / 12
CELL_KM = 9.3
TILE_CELLS = 12
REGION_TILES = 10
BASELINE = "1981-2010"
WATER_SUPPLY = "rainfed"
READ_WORKERS = 32
DB_TIMEOUT = 5
DB_RETRY_S = 60

INPUT_LEVELS = {"high": "H", "low": "L"}

CLASSES = [
    (8500, "very_high"),
    (7000, "high"),
    (5500, "good"),
    (4000, "medium"),
    (2500, "moderate"),
    (1000, "marginal"),
    (1, "very_marginal"),
    (0, "not_suitable"),
]
CLASS_NAMES = [name for _, name in CLASSES]
CLASS_FLOOR = {name: low for low, name in CLASSES}

CROP_GROUPS = {
    "whe": "cereal", "brl": "cereal", "mze": "cereal", "rye": "cereal",
    "oat": "cereal", "srg": "cereal", "bck": "cereal", "fml": "cereal",
    "pml": "cereal", "rcd": "cereal", "rcw": "cereal",
    "sfl": "oilseed", "rsd": "oilseed", "soy": "oilseed", "grd": "oilseed",
    "flx": "oilseed",
    "pea": "pulse", "chk": "pulse", "phb": "pulse", "cow": "pulse",
    "pig": "pulse", "grm": "pulse",
    "sub": "sugar", "suc": "sugar",
    "wpo": "root_tuber", "spo": "root_tuber", "csv": "root_tuber",
    "yam": "root_tuber",
    "cab": "vegetable", "car": "vegetable", "oni": "vegetable",
    "tom": "vegetable",
    "alf": "fodder", "nap": "fodder", "rcg": "fodder",
    "cot": "fibre", "tob": "other",
    "mis": "bioenergy", "swg": "bioenergy", "bsg": "bioenergy",
    "jtr": "bioenergy",
    "olv": "tree", "cit": "tree", "ban": "tree", "cof": "tree", "coc": "tree",
    "tea": "tree", "rub": "tree", "olp": "tree", "con": "tree",
}
CROP_GROUP_NAMES = sorted(set(CROP_GROUPS.values()))

YIELD_UNITS = {
    "kg DW/ha": (1, "dry_weight"),
    "10kg DW/ha": (10, "dry_weight"),
    "kg oil/ha": (1, "oil"),
    "kg sugar/ha": (1, "sugar"),
    "kg lint/ha": (1, "lint"),
}

DDL = {
    "gaez_legacy_cells": "DROP TABLE IF EXISTS gaez_plot_cell, gaez_cell_value, gaez_cell_fetch, gaez_cell",
    "gaez_crop": """
        CREATE TABLE IF NOT EXISTS gaez_crop (
            code       text PRIMARY KEY,
            name       text NOT NULL,
            crop_group text NOT NULL
        )""",
    "gaez_layer": """
        CREATE TABLE IF NOT EXISTS gaez_layer (
            layer_id     integer PRIMARY KEY,
            name         text NOT NULL UNIQUE,
            crop_code    text NOT NULL REFERENCES gaez_crop (code),
            variable     text NOT NULL CHECK (variable IN ('suitability', 'yield')),
            input_level  text NOT NULL CHECK (input_level IN ('high', 'low')),
            water_supply text NOT NULL,
            baseline     text NOT NULL,
            unit         text NOT NULL
        )""",
    "gaez_layer_url": "ALTER TABLE gaez_layer ADD COLUMN IF NOT EXISTS url text",
    "gaez_tile": """
        CREATE TABLE IF NOT EXISTS gaez_tile (
            tile_id serial PRIMARY KEY,
            row     integer NOT NULL,
            col     integer NOT NULL,
            north   double precision NOT NULL,
            west    double precision NOT NULL,
            UNIQUE (row, col)
        )""",
    "gaez_tile_fetch": """
        CREATE TABLE IF NOT EXISTS gaez_tile_fetch (
            tile_id     integer NOT NULL REFERENCES gaez_tile (tile_id) ON DELETE CASCADE,
            input_level text NOT NULL,
            fetched_at  timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tile_id, input_level)
        )""",
    "gaez_tile_value": """
        CREATE TABLE IF NOT EXISTS gaez_tile_value (
            tile_id  integer NOT NULL REFERENCES gaez_tile (tile_id) ON DELETE CASCADE,
            layer_id integer NOT NULL REFERENCES gaez_layer (layer_id),
            vals     smallint[] NOT NULL,
            PRIMARY KEY (tile_id, layer_id)
        )""",
}


class GaezError(RuntimeError):
    pass


@dataclass(frozen=True)
class Tile:
    row: int
    col: int

    @property
    def region(self) -> tuple[int, int]:
        return self.row // REGION_TILES, self.col // REGION_TILES

    @property
    def north_west(self) -> tuple[float, float]:
        return 90 - self.row, -180 + self.col


@dataclass(frozen=True)
class Cell:
    row: int
    col: int

    @classmethod
    def at(cls, lat: float, lon: float) -> "Cell":
        return cls(math.floor((90 - lat) / CELL_DEG), math.floor((lon + 180) / CELL_DEG))

    @property
    def center(self) -> tuple[float, float]:
        return 90 - (self.row + 0.5) * CELL_DEG, -180 + (self.col + 0.5) * CELL_DEG

    @property
    def tile(self) -> Tile:
        return Tile(self.row // TILE_CELLS, self.col // TILE_CELLS)

    @property
    def index(self) -> int:
        return (self.row % TILE_CELLS) * TILE_CELLS + self.col % TILE_CELLS


@dataclass(frozen=True)
class Layer:
    layer_id: int
    crop_code: str
    crop_name: str
    variable: str
    unit: str
    url: str


TileValues = dict[int, np.ndarray]


def suitability_class(index: float) -> str:
    return next(name for low, name in CLASSES if index >= low)


_db_down_until = 0.0


class DbSkipped(RuntimeError):
    pass


def _db_up() -> None:
    if time.monotonic() < _db_down_until:
        raise DbSkipped("database marked unreachable")
    for name, ddl in DDL.items():
        ensure_table(name, ddl)


def _db_failed(what: str, e: Exception) -> None:
    global _db_down_until
    if isinstance(e, DbSkipped):
        return
    _db_down_until = time.monotonic() + DB_RETRY_S
    log.warning("gaez %s failed, skipping the DB for %ss: %s", what, DB_RETRY_S, e)


_catalog: dict[str, list[Layer]] = {}
_catalog_lock = threading.Lock()

CATALOG_SQL = """
SELECT l.layer_id, l.crop_code, c.name, l.variable, l.unit, l.url
FROM gaez_layer l JOIN gaez_crop c ON c.code = l.crop_code
WHERE l.input_level = %s AND l.water_supply = %s AND l.baseline = %s
"""


def _fetch_catalog(level: str) -> list[Layer]:
    letter = INPUT_LEVELS[level]
    where = " OR ".join(f"name LIKE '{p}{letter}r\\_%' ESCAPE '\\'" for p in ("sx", "yx"))
    try:
        r = httpx.post(f"{SERVICE}/query", timeout=60, data={
            "where": f"({where}) AND year='{BASELINE}' AND rcp='Historical'",
            "outFields": "objectid,name,crop,units",
            "returnGeometry": "false",
            "f": "json",
        })
        r.raise_for_status()
        features = r.json()["features"]
    except (httpx.HTTPError, ValueError, KeyError) as e:
        raise GaezError(f"GAEZ catalog request failed: {e}") from e
    layers = []
    for f in features:
        a = f["attributes"]
        prefix, code = a["name"].split("_", 1)
        layers.append(Layer(
            a["objectid"], code, a["crop"],
            "suitability" if prefix.startswith("sx") else "yield",
            a["units"], TIFF_URL.format(level=letter, name=a["name"]),
        ))
    if not any(l.variable == "suitability" for l in layers):
        raise GaezError("GAEZ catalog returned no suitability layers")
    return layers


def _store_catalog(level: str, layers: list[Layer]) -> None:
    with pool.connection(timeout=DB_TIMEOUT) as conn, conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO gaez_crop (code, name, crop_group) VALUES (%s, %s, %s)
               ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, crop_group = EXCLUDED.crop_group""",
            sorted({(l.crop_code, l.crop_name, CROP_GROUPS.get(l.crop_code, "other")) for l in layers}),
        )
        cur.executemany(
            """INSERT INTO gaez_layer
                   (layer_id, name, crop_code, variable, input_level, water_supply, baseline, unit, url)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (layer_id) DO UPDATE SET url = EXCLUDED.url""",
            [(l.layer_id, l.url.rsplit("/", 1)[1].removesuffix(".tif"), l.crop_code, l.variable,
              level, WATER_SUPPLY, BASELINE, l.unit, l.url) for l in layers],
        )


def catalog(level: str) -> list[Layer]:
    with _catalog_lock:
        if level in _catalog:
            return _catalog[level]
        layers: list[Layer] = []
        try:
            _db_up()
            with pool.connection(timeout=DB_TIMEOUT) as conn:
                layers = [Layer(*r) for r in conn.execute(CATALOG_SQL, (level, WATER_SUPPLY, BASELINE))]
        except Exception as e:
            _db_failed("catalog read", e)
        if not layers or any(l.url is None for l in layers):
            with timed(f"gaez catalog {level}"):
                layers = _fetch_catalog(level)
            try:
                _db_up()
                _store_catalog(level, layers)
            except Exception as e:
                _db_failed("catalog write", e)
        _catalog[level] = layers
        return layers


_memory: dict[tuple[str, Tile], TileValues] = {}
_inflight: dict[tuple[str, tuple[int, int]], Future] = {}
_inflight_lock = threading.Lock()

TILE_FETCHED_SQL = """
SELECT t.tile_id FROM gaez_tile t
JOIN gaez_tile_fetch f ON f.tile_id = t.tile_id AND f.input_level = %(level)s
WHERE t.row = %(row)s AND t.col = %(col)s
"""

TILE_VALUES_SQL = """
SELECT v.layer_id, v.vals FROM gaez_tile_value v
JOIN gaez_layer l ON l.layer_id = v.layer_id AND l.input_level = %(level)s
WHERE v.tile_id = %(tile_id)s
"""


def _read_tile(level: str, tile: Tile) -> TileValues | None:
    args = {"level": level, "row": tile.row, "col": tile.col}
    try:
        _db_up()
        with pool.connection(timeout=DB_TIMEOUT) as conn:
            found = conn.execute(TILE_FETCHED_SQL, args).fetchone()
            if found is None:
                return None
            rows = conn.execute(TILE_VALUES_SQL, {**args, "tile_id": found[0]}).fetchall()
    except Exception as e:
        _db_failed("tile read", e)
        return None
    return {lid: np.array(vals, dtype=np.int16) for lid, vals in rows}


def _read_layer(layer: Layer, window: Window) -> np.ndarray:
    env = rasterio.Env(
        GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
        CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
        GDAL_HTTP_MULTIRANGE="YES",
        GDAL_HTTP_MAX_RETRY="3",
        GDAL_HTTP_RETRY_DELAY="1",
    )
    try:
        with env, rasterio.open(f"/vsicurl/{layer.url}") as src:
            return src.read(1, window=window)
    except rasterio.errors.RasterioError as e:
        raise GaezError(f"GAEZ read of {layer.url.rsplit('/', 1)[1]} failed: {e}") from e


def _fetch_region(level: str, region: tuple[int, int]) -> dict[Tile, TileValues]:
    layers = catalog(level)
    size = REGION_TILES * TILE_CELLS
    window = Window(region[1] * size, region[0] * size, size, size)
    with timed("gaez region read", logging.INFO, layers=len(layers), region=f"{region[0]},{region[1]}"):
        with ThreadPoolExecutor(max_workers=READ_WORKERS) as ex:
            grids = dict(zip((l.layer_id for l in layers), ex.map(lambda l: _read_layer(l, window), layers)))
    tiles: dict[Tile, TileValues] = {}
    for r in range(REGION_TILES):
        for c in range(REGION_TILES):
            tile = Tile(region[0] * REGION_TILES + r, region[1] * REGION_TILES + c)
            block = {
                lid: g[r * TILE_CELLS:(r + 1) * TILE_CELLS, c * TILE_CELLS:(c + 1) * TILE_CELLS].ravel()
                for lid, g in grids.items()
            }
            tiles[tile] = block if any((v >= 0).any() for v in block.values()) else {}
    return tiles


def _store_region(level: str, tiles: dict[Tile, TileValues]) -> None:
    try:
        _db_up()
        with timed("gaez region store", tiles=len(tiles)), \
                pool.connection(timeout=DB_TIMEOUT) as conn, conn.cursor() as cur:
            keys = list(tiles)
            ids = {
                (row, col): tid for tid, row, col in cur.execute(
                    """INSERT INTO gaez_tile (row, col, north, west)
                       SELECT * FROM unnest(%s::int[], %s::int[], %s::float8[], %s::float8[])
                       ON CONFLICT (row, col) DO UPDATE SET row = EXCLUDED.row
                       RETURNING tile_id, row, col""",
                    ([t.row for t in keys], [t.col for t in keys],
                     [float(t.north_west[0]) for t in keys], [float(t.north_west[1]) for t in keys]),
                ).fetchall()
            }
            tile_ids = [ids[(t.row, t.col)] for t in keys]
            cur.execute(
                "DELETE FROM gaez_tile_value WHERE tile_id = ANY(%s) AND layer_id = ANY(%s)",
                (tile_ids, [l.layer_id for l in catalog(level)]),
            )
            with cur.copy("COPY gaez_tile_value (tile_id, layer_id, vals) FROM STDIN") as copy:
                for tile, values in tiles.items():
                    for lid, vals in values.items():
                        copy.write_row((ids[(tile.row, tile.col)], lid, vals.tolist()))
            cur.executemany(
                """INSERT INTO gaez_tile_fetch (tile_id, input_level) VALUES (%s, %s)
                   ON CONFLICT (tile_id, input_level) DO UPDATE SET fetched_at = now()""",
                [(tid, level) for tid in tile_ids],
            )
    except Exception as e:
        _db_failed("region write", e)


def _load_region(level: str, region: tuple[int, int]) -> None:
    ident = (level, region)
    with _inflight_lock:
        running = _inflight.get(ident)
        if running is None:
            mine: Future = Future()
            _inflight[ident] = mine
    if running is not None:
        running.result()
        return
    try:
        tiles = _fetch_region(level, region)
        for tile, values in tiles.items():
            _memory[(level, tile)] = values
        _store_region(level, tiles)
        mine.set_result(None)
    except BaseException as e:
        mine.set_exception(e)
        raise
    finally:
        with _inflight_lock:
            _inflight.pop(ident, None)


def tile_values(level: str, tile: Tile) -> TileValues:
    key = (level, tile)
    if key in _memory:
        return _memory[key]
    values = _read_tile(level, tile)
    if values is not None:
        emit("gaez db hit", tile=f"{tile.row},{tile.col}", layers=len(values))
        _memory[key] = values
        return values
    _load_region(level, tile.region)
    return _memory[key]


def cells_values(level: str, cells: set[Cell]) -> dict[Cell, dict[int, float] | GaezError]:
    tiles: dict[Tile, TileValues | GaezError] = {}
    for tile in {c.tile for c in cells}:
        try:
            tiles[tile] = tile_values(level, tile)
        except GaezError as e:
            tiles[tile] = e
    out: dict[Cell, dict[int, float] | GaezError] = {}
    for cell in cells:
        got = tiles[cell.tile]
        if isinstance(got, GaezError):
            out[cell] = got
        else:
            out[cell] = {lid: float(v[cell.index]) for lid, v in got.items() if v[cell.index] >= 0}
    return out


_prefetch_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="gaez-prefetch")


def prefetch(level: str, cells: set[Cell]) -> int:
    def warm(tile: Tile) -> None:
        try:
            tile_values(level, tile)
        except Exception as e:
            log.warning("gaez prefetch of tile %s,%s failed: %s", tile.row, tile.col, e)

    missing = {c.tile for c in cells} - {t for lvl, t in _memory if lvl == level}
    for tile in missing:
        _prefetch_pool.submit(warm, tile)
    return len(missing)


def crop_list(
    level: str, values: dict[int, float], min_class: str, groups: set[str] | None
) -> list[dict]:
    floor = CLASS_FLOOR[min_class]
    by_crop: dict[str, dict] = {}
    for layer in catalog(level):
        if layer.layer_id not in values:
            continue
        entry = by_crop.setdefault(layer.crop_code, {"name": layer.crop_name})
        value = values[layer.layer_id]
        if layer.variable == "suitability":
            entry["si"] = value
        else:
            factor, basis = YIELD_UNITS.get(layer.unit, (1, "dry_weight"))
            entry["yield"] = round(value * factor)
            entry["basis"] = basis

    crops = []
    for code, v in by_crop.items():
        si = v.get("si")
        group = CROP_GROUPS.get(code, "other")
        if si is None or si < floor or (groups and group not in groups):
            continue
        crops.append({
            "crop": v["name"],
            "code": code,
            "group": group,
            "suitability": round(si / 100, 1),
            "class": suitability_class(si),
            "attainable_yield_kg_ha": v.get("yield"),
            "yield_basis": v.get("basis"),
        })
    crops.sort(key=lambda c: (-c["suitability"], -(c["attainable_yield_kg_ha"] or 0)))
    return crops
