"""Crop suitability from FAO GAEZ v4 (Global Agro-Ecological Zones).

GAEZ has already rated every ~9 km grid cell (5 arc-minutes) of the world for
about 50 crops from climate, terrain and soil. We read two of its layers for
the 1981-2010 baseline climate and rainfed farming, at high or low input level:

- suitability index, 0-10000, over all land in the cell (`sx{H|L}r_<crop>`)
- attainable yield of the best suitability class in the cell (`yx{H|L}r_<crop>`)

FAO serves them from an ArcGIS ImageServer. One `identify` call that locks all
of a level's ~50-100 layers returns every crop's value at a point (about 5 s).

Everything is kept in Postgres as plain tables, keyed by GAEZ's own ids:

    gaez_crop        code -> name, group
    gaez_layer       GAEZ layer objectid -> crop, variable, input level, unit
    gaez_cell        one row per grid cell we have looked at (row, col, centre)
    gaez_cell_fetch  which input levels of a cell have been fetched, and when
    gaez_cell_value  (cell, layer) -> value
    gaez_plot_cell   plot (field id or outline hash) -> cell

GAEZ is static, so nothing expires. If the database is unreachable, requests still
work: after one failed call the DB is skipped for DB_RETRY_S, and cells seen by
this process are also kept in memory. A cell is far larger than a field: every
plot in the same cell gets the same answer. It is regional potential, not a
reading of the field itself.
"""

import logging
import math
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass

import httpx

from app.db import ensure_table, pool
from app.timing import emit, timed

log = logging.getLogger("app.gaez")

SERVICE = "https://gaez-services.fao.org/server/rest/services/res05/ImageServer"
CELL_DEG = 1 / 12  # 5 arc-minutes
CELL_KM = 9.3
BASELINE = "1981-2010"
WATER_SUPPLY = "rainfed"
CELL_WORKERS = 4
DB_TIMEOUT = 5
DB_RETRY_S = 60  # after a failed DB call, skip the DB this long instead of timing out on every step

INPUT_LEVELS = {"high": "H", "low": "L"}

# lower bound of each GAEZ v4 class on the 0-10000 index (checked against
# FAO's own class rasters, siHr_*)
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

# GAEZ crop code -> group, so the frontend can hide what makes no sense for
# Bulgarian arable land (tree crops, tropical crops, energy grasses)
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

# GAEZ yield units -> (kg per unit, what the yield measures)
YIELD_UNITS = {
    "kg DW/ha": (1, "dry_weight"),
    "10kg DW/ha": (10, "dry_weight"),
    "kg oil/ha": (1, "oil"),
    "kg sugar/ha": (1, "sugar"),
    "kg lint/ha": (1, "lint"),
}

DDL = {
    "gaez_crop": """
        CREATE TABLE IF NOT EXISTS gaez_crop (
            code       text PRIMARY KEY,           -- GAEZ crop code, e.g. 'whe'
            name       text NOT NULL,
            crop_group text NOT NULL
        )""",
    "gaez_layer": """
        CREATE TABLE IF NOT EXISTS gaez_layer (
            layer_id     integer PRIMARY KEY,      -- GAEZ ImageServer objectid
            name         text NOT NULL UNIQUE,     -- e.g. 'sxHr_whe'
            crop_code    text NOT NULL REFERENCES gaez_crop (code),
            variable     text NOT NULL CHECK (variable IN ('suitability', 'yield')),
            input_level  text NOT NULL CHECK (input_level IN ('high', 'low')),
            water_supply text NOT NULL,
            baseline     text NOT NULL,
            unit         text NOT NULL
        )""",
    "gaez_cell": """
        CREATE TABLE IF NOT EXISTS gaez_cell (
            cell_id serial PRIMARY KEY,
            row     integer NOT NULL,              -- from 90N, CELL_DEG steps
            col     integer NOT NULL,              -- from 180W, CELL_DEG steps
            lat     double precision NOT NULL,     -- centre
            lon     double precision NOT NULL,
            UNIQUE (row, col)
        )""",
    "gaez_cell_fetch": """
        CREATE TABLE IF NOT EXISTS gaez_cell_fetch (
            cell_id     integer NOT NULL REFERENCES gaez_cell (cell_id) ON DELETE CASCADE,
            input_level text NOT NULL,
            fetched_at  timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (cell_id, input_level)
        )""",
    "gaez_cell_value": """
        CREATE TABLE IF NOT EXISTS gaez_cell_value (
            cell_id  integer NOT NULL REFERENCES gaez_cell (cell_id) ON DELETE CASCADE,
            layer_id integer NOT NULL REFERENCES gaez_layer (layer_id),
            value    real NOT NULL,                -- raw GAEZ value, in the layer's unit
            PRIMARY KEY (cell_id, layer_id)
        )""",
    "gaez_plot_cell": """
        CREATE TABLE IF NOT EXISTS gaez_plot_cell (
            plot_key   text PRIMARY KEY,           -- 'field:<id>' or 'outline:<sha256>'
            cell_id    integer NOT NULL REFERENCES gaez_cell (cell_id) ON DELETE CASCADE,
            created_at timestamptz NOT NULL DEFAULT now()
        )""",
}


class GaezError(RuntimeError):
    pass


@dataclass(frozen=True)
class Cell:
    row: int
    col: int

    @classmethod
    def at(cls, lat: float, lon: float) -> "Cell":
        return cls(math.floor((90 - lat) / CELL_DEG), math.floor((lon + 180) / CELL_DEG))

    @property
    def center(self) -> tuple[float, float]:
        """(lat, lon) of the cell centre."""
        return 90 - (self.row + 0.5) * CELL_DEG, -180 + (self.col + 0.5) * CELL_DEG


@dataclass(frozen=True)
class Layer:
    layer_id: int
    crop_code: str
    crop_name: str
    variable: str  # suitability | yield
    unit: str


def suitability_class(index: float) -> str:
    return next(name for low, name in CLASSES if index >= low)


_db_down_until = 0.0
# cells seen by this process; answers keep coming while the DB is unreachable
_memory: dict[tuple[str, "Cell"], dict[int, float]] = {}


class DbSkipped(RuntimeError):
    pass


def _db_up() -> None:
    """Raise DbSkipped while a recent DB failure is being waited out."""
    if time.monotonic() < _db_down_until:
        raise DbSkipped("database marked unreachable")
    for name, ddl in DDL.items():  # in dependency order
        ensure_table(name, ddl)


def _db_failed(what: str, e: Exception) -> None:
    global _db_down_until
    if isinstance(e, DbSkipped):
        return
    _db_down_until = time.monotonic() + DB_RETRY_S
    log.warning("gaez %s failed, skipping the DB for %ss: %s", what, DB_RETRY_S, e)


def _gaez(path: str, params: dict) -> dict:
    try:
        r = httpx.post(f"{SERVICE}/{path}", data={**params, "f": "json"}, timeout=90)
        r.raise_for_status()
        data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise GaezError(f"GAEZ {path} request failed: {e}") from e
    if "error" in data:
        raise GaezError(f"GAEZ {path} error: {data['error'].get('message', data['error'])}")
    return data


# ------------------------------------------------------------------- catalog

_catalog: dict[str, list[Layer]] = {}
_catalog_lock = threading.Lock()

CATALOG_SQL = """
SELECT l.layer_id, l.crop_code, c.name, l.variable, l.unit
FROM gaez_layer l JOIN gaez_crop c ON c.code = l.crop_code
WHERE l.input_level = %s AND l.water_supply = %s AND l.baseline = %s
"""


def _fetch_catalog(level: str) -> list[dict]:
    """This level's suitability and yield layers, straight from GAEZ."""
    letter = INPUT_LEVELS[level]
    where = " OR ".join(
        f"name LIKE '{p}{letter}r\\_%' ESCAPE '\\'" for p in ("sx", "yx")
    )
    data = _gaez("query", {
        "where": f"({where}) AND year='{BASELINE}' AND rcp='Historical'",
        "outFields": "objectid,name,crop,units",
        "returnGeometry": "false",
    })
    rows = []
    for f in data.get("features", []):
        a = f["attributes"]
        prefix, code = a["name"].split("_", 1)
        rows.append({
            "layer_id": a["objectid"], "name": a["name"], "crop_code": code,
            "crop_name": a["crop"], "unit": a["units"],
            "variable": "suitability" if prefix.startswith("sx") else "yield",
        })
    if not any(r["variable"] == "suitability" for r in rows):
        raise GaezError("GAEZ catalog returned no suitability layers")
    return rows


def _store_catalog(level: str, rows: list[dict]) -> None:
    with pool.connection(timeout=DB_TIMEOUT) as conn, conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO gaez_crop (code, name, crop_group) VALUES (%s, %s, %s)
               ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, crop_group = EXCLUDED.crop_group""",
            sorted({(r["crop_code"], r["crop_name"], CROP_GROUPS.get(r["crop_code"], "other")) for r in rows}),
        )
        cur.executemany(
            """INSERT INTO gaez_layer
                   (layer_id, name, crop_code, variable, input_level, water_supply, baseline, unit)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (layer_id) DO NOTHING""",
            [(r["layer_id"], r["name"], r["crop_code"], r["variable"], level, WATER_SUPPLY,
              BASELINE, r["unit"]) for r in rows],
        )


def catalog(level: str) -> list[Layer]:
    """This level's layers: from memory, else gaez_layer, else GAEZ (then stored)."""
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
        if not layers:
            with timed(f"gaez catalog {level}"):
                rows = _fetch_catalog(level)
            try:
                _db_up()
                _store_catalog(level, rows)
            except Exception as e:
                _db_failed("catalog write", e)
            layers = [
                Layer(r["layer_id"], r["crop_code"], r["crop_name"], r["variable"], r["unit"])
                for r in rows
            ]
        _catalog[level] = layers
        return layers


# --------------------------------------------------------------------- cells

CELL_UPSERT_SQL = """
INSERT INTO gaez_cell (row, col, lat, lon) VALUES (%s, %s, %s, %s)
ON CONFLICT (row, col) DO UPDATE SET row = EXCLUDED.row
RETURNING cell_id
"""

CELL_VALUES_SQL = """
SELECT v.layer_id, v.value
FROM gaez_cell c
JOIN gaez_cell_fetch f ON f.cell_id = c.cell_id AND f.input_level = %(level)s
JOIN gaez_cell_value v ON v.cell_id = c.cell_id
JOIN gaez_layer l ON l.layer_id = v.layer_id AND l.input_level = %(level)s
WHERE c.row = %(row)s AND c.col = %(col)s
"""

CELL_FETCHED_SQL = """
SELECT 1 FROM gaez_cell c
JOIN gaez_cell_fetch f ON f.cell_id = c.cell_id AND f.input_level = %(level)s
WHERE c.row = %(row)s AND c.col = %(col)s
"""

_inflight: dict[tuple, Future] = {}
_inflight_lock = threading.Lock()


def _cell_id(conn, cell: Cell) -> int:
    lat, lon = cell.center
    return conn.execute(CELL_UPSERT_SQL, (cell.row, cell.col, lat, lon)).fetchone()[0]


def _read_cell(level: str, cell: Cell) -> dict[int, float] | None:
    """{layer_id: value} if this level of the cell was fetched before, else None."""
    args = {"level": level, "row": cell.row, "col": cell.col}
    try:
        _db_up()
        with pool.connection(timeout=DB_TIMEOUT) as conn:
            if conn.execute(CELL_FETCHED_SQL, args).fetchone() is None:
                return None
            return {lid: v for lid, v in conn.execute(CELL_VALUES_SQL, args)}
    except Exception as e:
        _db_failed("cell read", e)
        return None


def _fetch_cell(layers: list[Layer], cell: Cell) -> dict[int, float]:
    """{layer_id: value} at the cell centre, straight from GAEZ; sea / no data left out."""
    lat, lon = cell.center
    ids = [l.layer_id for l in layers]
    with timed("gaez identify", layers=len(ids), cell=f"{cell.row},{cell.col}"):
        data = _gaez("identify", {
            "geometry": f'{{"x":{lon},"y":{lat},"spatialReference":{{"wkid":4326}}}}',
            "geometryType": "esriGeometryPoint",
            "mosaicRule": f'{{"mosaicMethod":"esriMosaicLockRaster","lockRasterIds":{ids}}}',
            "returnCatalogItems": "true",
            "returnGeometry": "false",
        })
    # one value per catalog item, in the same order
    items = (data.get("catalogItems") or {}).get("features") or []
    values = (data.get("properties") or {}).get("Values") or []
    if not items or len(items) != len(values):
        raise GaezError("GAEZ identify returned no values for this location")
    out = {}
    for item, raw in zip(items, values):
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue  # "NoData"
        if value >= 0:  # -9: sea / no data
            out[item["attributes"]["objectid"]] = value
    return out


def _store_cell(level: str, cell: Cell, values: dict[int, float]) -> None:
    try:
        _db_up()
        with pool.connection(timeout=DB_TIMEOUT) as conn, conn.cursor() as cur:
            cell_id = _cell_id(conn, cell)
            cur.executemany(
                """INSERT INTO gaez_cell_value (cell_id, layer_id, value) VALUES (%s, %s, %s)
                   ON CONFLICT (cell_id, layer_id) DO UPDATE SET value = EXCLUDED.value""",
                [(cell_id, lid, v) for lid, v in values.items()],
            )
            # written last: a cell only counts as fetched once its values are in
            cur.execute(
                """INSERT INTO gaez_cell_fetch (cell_id, input_level) VALUES (%s, %s)
                   ON CONFLICT (cell_id, input_level) DO UPDATE SET fetched_at = now()""",
                (cell_id, level),
            )
    except Exception as e:
        _db_failed("cell write", e)


def cell_values(level: str, cell: Cell) -> dict[int, float]:
    """{layer_id: value} for one cell: from Postgres, else from GAEZ (then stored).

    Plots in the same cell asked for at the same time share one GAEZ request."""
    ident = (level, cell)
    with _inflight_lock:
        running = _inflight.get(ident)
        if running is None:
            mine: Future = Future()
            _inflight[ident] = mine
    if running is not None:
        return running.result()
    try:
        values = _memory.get(ident)
        if values is None:
            values = _read_cell(level, cell)
        if values is not None:
            emit("gaez cache hit", cell=f"{cell.row},{cell.col}", values=len(values))
        else:
            values = _fetch_cell(catalog(level), cell)
            _store_cell(level, cell, values)
        _memory[ident] = values
        mine.set_result(values)
        return values
    except BaseException as e:
        mine.set_exception(e)
        raise
    finally:
        with _inflight_lock:
            _inflight.pop(ident, None)


def cells_values(level: str, cells: set[Cell]) -> dict[Cell, dict[int, float] | GaezError]:
    """Values for several cells, fetched in parallel; a failed cell maps to its error."""

    def one(cell: Cell):
        try:
            return cell, cell_values(level, cell)
        except GaezError as e:
            return cell, e

    with ThreadPoolExecutor(max_workers=CELL_WORKERS) as ex:
        return dict(ex.map(one, cells))


# --------------------------------------------------------------------- plots

def plot_cells(plots: list[tuple[str, float, float]]) -> list[Cell]:
    """Cell of each (plot_key, lat, lon), via gaez_plot_cell, which is filled as
    plots are first seen. The point is any point inside the plot."""
    cells = [Cell.at(lat, lon) for _, lat, lon in plots]
    try:
        _db_up()
        with pool.connection(timeout=DB_TIMEOUT) as conn, conn.cursor() as cur:
            keys = [k for k, _, _ in plots]
            known = dict(cur.execute(
                """SELECT p.plot_key, c.row || ',' || c.col FROM gaez_plot_cell p
                   JOIN gaez_cell c USING (cell_id) WHERE p.plot_key = ANY(%s)""",
                (keys,),
            ).fetchall())
            for i, key in enumerate(keys):
                if key in known:
                    row, col = map(int, known[key].split(","))
                    cells[i] = Cell(row, col)
                else:
                    cur.execute(
                        """INSERT INTO gaez_plot_cell (plot_key, cell_id) VALUES (%s, %s)
                           ON CONFLICT (plot_key) DO NOTHING""",
                        (key, _cell_id(conn, cells[i])),
                    )
    except Exception as e:
        _db_failed("plot mapping", e)
    return cells


def crop_list(
    level: str, values: dict[int, float], min_class: str, groups: set[str] | None
) -> list[dict]:
    """Crops at or above `min_class`, best first."""
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
            "suitability": round(si / 100, 1),  # 0-100
            "class": suitability_class(si),
            "attainable_yield_kg_ha": v.get("yield"),
            "yield_basis": v.get("basis"),
        })
    crops.sort(key=lambda c: (-c["suitability"], -(c["attainable_yield_kg_ha"] or 0)))
    return crops
