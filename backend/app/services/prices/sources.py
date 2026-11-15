"""Raw price inputs: Eurostat crop prices, FX and HICP, and the FAOSTAT bulk file."""

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


def fetch_fao_bulk(item_codes: set[int]) -> list[FaoPrice]:
    try:
        r = httpx.get(FAOSTAT_BULK, timeout=httpx.Timeout(30, read=300), follow_redirects=True)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise PriceSourceError(f"FAOSTAT bulk download failed: {e}") from e
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        name = next(n for n in z.namelist() if "All_Data" in n and n.endswith(".csv"))
        with z.open(name) as raw:
            return parse_fao_bulk(io.TextIOWrapper(raw, encoding="latin-1", newline=""), item_codes)
