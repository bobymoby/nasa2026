"""Raw inputs: Eurostat crop prices, yields, FX and HICP, and the FAOSTAT price and production bulk files."""

import csv
import io
import re
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

import httpx

EUROSTAT_API = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"
EUROSTAT_PRICES = "apri_ap_crpouta"
EUROSTAT_FX = "ert_bil_eur_a"
EUROSTAT_HICP = "prc_hicp_aind"
FAOSTAT_BULK = "https://bulks-faostat.fao.org/production/Prices_E_All_Data_(Normalized).zip"
EUROSTAT_YIELDS = "apro_cpsh1"
EUROSTAT_YIELD_MEASURE = "YLD_HUMD_EU_T_HA"
FAOSTAT_YIELD_BULK = "https://bulks-faostat.fao.org/production/Production_Crops_Livestock_E_All_Data_(Normalized).zip"
FAO_YIELD = "5412"
FAO_YIELD_T_PER_UNIT = {"kg/ha": 0.001, "100 g/ha": 0.0001, "hg/ha": 0.0001}
FAO_YIELD_SINCE = 2000
FAO_USD_PER_TONNE = "5532"
FAO_ANNUAL = "7021"
TIMEOUT = httpx.Timeout(30, read=120)
NO_DATA_STATUSES = {400, 404}  # Eurostat's answer for a country or code it has no series for

_KG_IN_LABEL = re.compile(r"per\s+(\d+)\s*kg", re.IGNORECASE)


class PriceSourceError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class EurostatPrice:
    prod_veg: str
    year: int
    eur_per_t: float
    flag: str | None


@dataclass(frozen=True)
class FaoPrice:
    m49: int
    item_code: int
    year: int
    usd_per_t: float
    flag: str


@dataclass(frozen=True)
class EurostatYield:
    crops: str
    year: int
    t_ha: float
    flag: str | None


@dataclass(frozen=True)
class FaoYield:
    m49: int
    item_code: int
    year: int
    t_ha: float
    flag: str


def _get_json(url: str, params: list[tuple[str, str]]) -> dict:
    try:
        r = httpx.get(url, params=params, timeout=TIMEOUT)
    except httpx.HTTPError as e:
        raise PriceSourceError(f"{url} request failed: {e}") from e
    try:
        data = r.json()
    except ValueError as e:
        raise PriceSourceError(f"{url} returned {r.status_code}, not JSON") from e
    if r.status_code != 200 or "error" in data:
        raise PriceSourceError(f"{url} returned {r.status_code}: {data.get('error', data)}", r.status_code)
    return data


def jsonstat_cells(data: dict) -> Iterator[tuple[dict[str, str], float, str | None]]:
    """(category code per dimension, value, status flag) for every non-empty cell."""
    dims = data["id"]
    sizes = data["size"]
    codes = []
    for dim in dims:
        index = data["dimension"][dim]["category"]["index"]
        by_pos = sorted(index, key=index.get) if isinstance(index, dict) else list(index)
        codes.append(by_pos)
    status = data.get("status") or {}
    for flat, value in data.get("value", {}).items():
        if value is None:
            continue
        pos, rest = [], int(flat)
        for size in reversed(sizes):
            rest, p = divmod(rest, size)
            pos.append(p)
        pos.reverse()
        yield {dim: codes[i][p] for i, (dim, p) in enumerate(zip(dims, pos))}, float(value), status.get(flat)


def kg_per_unit(label: str) -> int | None:
    m = _KG_IN_LABEL.search(label)
    return int(m.group(1)) if m else None


def parse_eurostat_prices(data: dict) -> list[EurostatPrice]:
    labels = data["dimension"]["prod_veg"]["category"].get("label", {})
    out = []
    for cell, value, flag in jsonstat_cells(data):
        kg = kg_per_unit(labels.get(cell["prod_veg"], ""))
        if kg is None:
            continue
        out.append(EurostatPrice(cell["prod_veg"], int(cell["time"]), value * 1000 / kg, flag))
    return out


def fetch_eurostat_prices(geo: str, prod_codes: Iterable[str]) -> list[EurostatPrice]:
    params = [("geo", geo), ("currency", "EUR"), ("lang", "EN")]
    params += [("prod_veg", code) for code in sorted(set(prod_codes))]
    try:
        data = _get_json(f"{EUROSTAT_API}/{EUROSTAT_PRICES}", params)
    except PriceSourceError as e:
        if e.status in NO_DATA_STATUSES:
            return []
        raise
    return parse_eurostat_prices(data)


def parse_eurostat_yields(data: dict) -> list[EurostatYield]:
    return [
        EurostatYield(cell["crops"], int(cell["time"]), value, flag)
        for cell, value, flag in jsonstat_cells(data)
        if value > 0
    ]


def fetch_eurostat_yields(geo: str, crop_codes: Iterable[str]) -> list[EurostatYield]:
    params = [("geo", geo), ("strucpro", EUROSTAT_YIELD_MEASURE), ("lang", "EN")]
    params += [("crops", code) for code in sorted(set(crop_codes))]
    try:
        data = _get_json(f"{EUROSTAT_API}/{EUROSTAT_YIELDS}", params)
    except PriceSourceError as e:
        if e.status in NO_DATA_STATUSES:
            return []
        raise
    return parse_eurostat_yields(data)


def _yearly(data: dict, key_dim: str | None = None) -> dict[str, dict[int, float]]:
    out: dict[str, dict[int, float]] = {}
    for cell, value, _ in jsonstat_cells(data):
        out.setdefault(cell[key_dim] if key_dim else "", {})[int(cell["time"])] = value
    return out


def fetch_usd_per_eur() -> dict[int, float]:
    data = _get_json(f"{EUROSTAT_API}/{EUROSTAT_FX}", [("currency", "USD"), ("statinfo", "AVG"), ("lang", "EN")])
    return _yearly(data).get("", {})


def fetch_hicp(geos: Iterable[str]) -> dict[str, dict[int, float]]:
    params = [("coicop", "CP00"), ("unit", "INX_A_AVG"), ("lang", "EN")]
    params += [("geo", g) for g in sorted(set(geos))]
    try:
        data = _get_json(f"{EUROSTAT_API}/{EUROSTAT_HICP}", params)
    except PriceSourceError as e:
        if e.status in NO_DATA_STATUSES:
            return {}
        raise
    return _yearly(data, "geo")


def parse_fao_bulk(lines: Iterable[str], item_codes: set[int]) -> list[FaoPrice]:
    out = []
    for row in csv.DictReader(lines):
        if row["Element Code"] != FAO_USD_PER_TONNE or row["Months Code"] != FAO_ANNUAL:
            continue
        item = int(row["Item Code"])
        if item not in item_codes or not row["Value"]:
            continue
        m49 = row["Area Code (M49)"].lstrip("'")
        if not m49.isdigit():
            continue
        out.append(FaoPrice(int(m49), item, int(row["Year"]), float(row["Value"]), row["Flag"]))
    return out


def parse_fao_yields(lines: Iterable[str], item_codes: set[int]) -> list[FaoYield]:
    rows = csv.reader(lines)
    col = {name: i for i, name in enumerate(next(rows))}
    element, item_col, area, year_col = col["Element Code"], col["Item Code"], col["Area Code (M49)"], col["Year"]
    unit, value_col, flag = col["Unit"], col["Value"], col["Flag"]
    wanted = {str(code) for code in item_codes}
    out = []
    for row in rows:
        if row[element] != FAO_YIELD or row[item_col] not in wanted or not row[value_col]:
            continue
        year = int(row[year_col])
        scale = FAO_YIELD_T_PER_UNIT.get(row[unit])
        m49 = row[area].lstrip("'")
        if year < FAO_YIELD_SINCE or scale is None or not m49.isdigit():
            continue
        t_ha = float(row[value_col]) * scale
        if t_ha > 0:
            out.append(FaoYield(int(m49), int(row[item_col]), year, round(t_ha, 4), row[flag]))
    return out


def _bulk_csv(url: str, label: str, parse):
    try:
        r = httpx.get(url, timeout=httpx.Timeout(30, read=300), follow_redirects=True)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise PriceSourceError(f"{label} download failed: {e}") from e
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        name = next(n for n in z.namelist() if "All_Data" in n and n.endswith(".csv"))
        with z.open(name) as raw:
            return parse(io.TextIOWrapper(raw, encoding="latin-1", newline=""))


def fetch_fao_bulk(item_codes: set[int]) -> list[FaoPrice]:
    return _bulk_csv(FAOSTAT_BULK, "FAOSTAT bulk", lambda lines: parse_fao_bulk(lines, item_codes))


def fetch_fao_yields(item_codes: set[int]) -> list[FaoYield]:
    return _bulk_csv(FAOSTAT_YIELD_BULK, "FAOSTAT production bulk", lambda lines: parse_fao_yields(lines, item_codes))
