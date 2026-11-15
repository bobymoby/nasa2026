"""Crop selling prices per country, standardised to € per tonne in today's money.

Eurostat (apri_ap_crpouta, ex-farm, national) is the main source; FAOSTAT
producer prices (USD/t, converted at that year's Eurostat USD/EUR rate) fill
any crop or year Eurostat lacks and cover non-EU countries. Values are deflated
with the country's HICP where Eurostat publishes its prices, otherwise with
the euro area's, to the latest HICP year.
"""

import csv
import logging
import statistics
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import TypeVar

import pycountry

from app.services.prices import sources, store
from app.services.prices.sources import EurostatPrice, FaoPrice, PriceSourceError
from app.timing import timed

log = logging.getLogger("app.prices")

MAPPING_CSV = Path(__file__).resolve().parents[2] / "data" / "crop_price_codes.csv"
EUROSTAT_GEO_ALIASES = {"GR": "EL", "GB": "UK"}
FALLBACK_DEFLATOR = "EA"
EUROSTAT_MAX_AGE_DAYS = 7
FAOSTAT_MAX_AGE_DAYS = 30
SERIES_MAX_AGE_DAYS = 7
VARIABILITY_YEARS = 10
MIN_VARIABILITY_YEARS = 3
VARIABILITY_LEVELS = [(0.15, "low"), (0.30, "medium"), (float("inf"), "high")]
CONFIDENCE_ORDER = ["low", "medium", "high"]

T = TypeVar("T")


@dataclass(frozen=True)
class CropCodes:
    crop_id: str
    crop_name: str
    eurostat_prod_veg: str | None
    fao_item_code: int | None


@dataclass(frozen=True)
class Country:
    code: str
    name: str
    eurostat_geo: str
    m49: int


@dataclass(frozen=True)
class PriceRow:
    country: str
    crop_id: str
    year: int
    eur_per_t: float
    nominal_eur_per_t: float
    source: str
    flag: str | None
    confidence: str


@dataclass(frozen=True)
class CountryPrices:
    country: Country
    price_year: int
    deflator: str
    rows: list[PriceRow]


@cache
def crop_codes() -> dict[str, CropCodes]:
    with MAPPING_CSV.open(newline="") as f:
        codes = {
            r["crop_id"]: CropCodes(
                r["crop_id"], r["crop_name"], r["eurostat_prod_veg"] or None,
                int(r["fao_item_code"]) if r["fao_item_code"] else None,
            )
            for r in csv.DictReader(f)
        }
    store.sync_crops((c.crop_id, c.crop_name, c.eurostat_prod_veg, c.fao_item_code) for c in codes.values())
    return codes


def resolve_country(code: str) -> Country:
    c = pycountry.countries.get(alpha_2=code.upper())
    if c is None or not getattr(c, "numeric", None):
        raise ValueError(f"unknown ISO 3166 country code {code!r}")
    alpha2 = c.alpha_2
    return Country(alpha2, c.name, EUROSTAT_GEO_ALIASES.get(alpha2, alpha2), int(c.numeric))


def confidence_for(source: str, flag: str | None) -> str:
    if source == "eurostat":
        return "high"
    return "medium" if flag == "A" else "low"


_memory: dict[tuple[str, str], tuple[float, object]] = {}
_locks: dict[tuple[str, str], threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock(ident: tuple[str, str]) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(ident, threading.Lock())


def _cached(
    source: str, key: str, max_age_days: int,
    read: Callable[[], T | None], fetch: Callable[[], T], write: Callable[[T], None],
) -> T:
    """Postgres first, then the source; a failed refresh falls back to stale data."""
    ident = (source, key)
    with _lock(ident):
        held = _memory.get(ident)
        if held and time.monotonic() - held[0] < max_age_days * 86400:
            return held[1]  # type: ignore[return-value]
        if store.is_fresh(source, key, max_age_days):
            data = read()
            if data is not None:
                _memory[ident] = (time.monotonic(), data)
                return data
        try:
            with timed(f"prices fetch {source} {key}", logging.INFO):
                data = fetch()
        except PriceSourceError:
            stale = read()
            if stale:
                log.warning("prices: %s %s refresh failed, using stored data", source, key)
                return stale
            raise
        write(data)
        _memory[ident] = (time.monotonic(), data)
        return data


def _eurostat(geo: str) -> list[EurostatPrice]:
    codes = {c.eurostat_prod_veg for c in crop_codes().values() if c.eurostat_prod_veg}
    return _cached(
        "eurostat", geo, EUROSTAT_MAX_AGE_DAYS,
        lambda: store.get_eurostat(geo),
        lambda: sources.fetch_eurostat_prices(geo, codes),
        lambda data: store.put_eurostat(geo, data),
    )


_fao_all: list[FaoPrice] | None = None
_fao_loaded_at = 0.0
_fao_lock = threading.Lock()


def _ensure_fao_bulk() -> None:
    """The FAOSTAT file covers every country, so it is fetched whole and stored once a month."""
    global _fao_all, _fao_loaded_at
    with _fao_lock:
        if time.monotonic() - _fao_loaded_at < FAOSTAT_MAX_AGE_DAYS * 86400:
            return
        if store.is_fresh("faostat", "bulk", FAOSTAT_MAX_AGE_DAYS):
            _fao_loaded_at = time.monotonic()
            return
        items = {c.fao_item_code for c in crop_codes().values() if c.fao_item_code}
        try:
            with timed("prices fetch faostat bulk", logging.INFO):
                _fao_all = sources.fetch_fao_bulk(items)
        except PriceSourceError:
            if store.has_fao():
                log.warning("prices: FAOSTAT refresh failed, using stored data")
                _fao_loaded_at = time.monotonic()
                return
            raise
        store.put_fao(_fao_all)
        _fao_loaded_at = time.monotonic()


def _fao(m49: int) -> list[FaoPrice]:
    _ensure_fao_bulk()
    stored = store.get_fao(m49)
    if stored is not None:
        return stored
    return [p for p in _fao_all or [] if p.m49 == m49]


def _series(table: str, key_col: str, source: str, key: str, fetch: Callable[[], dict[int, float]]) -> dict[int, float]:
    return _cached(
        source, key, SERIES_MAX_AGE_DAYS,
        lambda: store.get_series(table, key_col, key),
        fetch,
        lambda data: store.put_series(table, key_col, key, data, source),
    )


def _usd_per_eur() -> dict[int, float]:
    return _series("eurostat_fx", "currency", "fx", "USD", sources.fetch_usd_per_eur)


def _hicp(geo: str) -> dict[int, float]:
    return _series("eurostat_hicp", "geo", "hicp", geo, lambda: sources.fetch_hicp([geo]).get(geo, {}))


def _deflator(geo: str, has_eurostat_prices: bool) -> tuple[str, dict[int, float]]:
    """National HICP for countries Eurostat prices; the euro area's for prices converted from USD."""
    own = _hicp(geo) if has_eurostat_prices else {}
    if own:
        return geo, own
    return FALLBACK_DEFLATOR, _hicp(FALLBACK_DEFLATOR)


def merge_rows(
    country: str,
    crops: list[CropCodes],
    eurostat: list[EurostatPrice],
    fao: list[FaoPrice],
    usd_per_eur: dict[int, float],
    index: dict[int, float],
) -> tuple[int, list[PriceRow]]:
    """Eurostat where it has a value, FAOSTAT for the rest; real € per tonne at the latest index year."""
    if not index:
        raise PriceSourceError("no HICP series to deflate with")
    base_year = max(index)
    es = {(p.prod_veg, p.year): p for p in eurostat}
    fa = {(p.item_code, p.year): p for p in fao}
    rows = []
    for crop in crops:
        years: dict[int, tuple[float, str, str | None]] = {}
        if crop.fao_item_code:
            for (item, year), p in fa.items():
                if item == crop.fao_item_code and year in usd_per_eur:
                    years[year] = (p.usd_per_t / usd_per_eur[year], "faostat", p.flag)
        if crop.eurostat_prod_veg:
            for (code, year), p in es.items():
                if code == crop.eurostat_prod_veg:
                    years[year] = (p.eur_per_t, "eurostat", p.flag)
        for year, (nominal, source, flag) in sorted(years.items()):
            if year not in index or year > base_year:
                continue
            rows.append(PriceRow(
                country, crop.crop_id, year,
                round(nominal * index[base_year] / index[year], 2), round(nominal, 2),
                source, flag, confidence_for(source, flag),
            ))
    return base_year, rows


def country_prices(code: str, crop_ids: list[str] | None = None) -> CountryPrices:
    country = resolve_country(code)
    mapping = crop_codes()
    crops = [mapping[c] for c in (crop_ids or list(mapping)) if c in mapping]
    eurostat = _eurostat(country.eurostat_geo)
    deflator, index = _deflator(country.eurostat_geo, bool(eurostat))
    base_year, rows = merge_rows(country.code, crops, eurostat, _fao(country.m49), _usd_per_eur(), index)
    return CountryPrices(country, base_year, deflator, rows)


def summarize(rows: list[PriceRow], reference_years: int, override: float | None) -> dict:
    """Reference price (mean of the last N years with data), variability and history for one crop."""
    rows = sorted(rows, key=lambda r: r.year)
    recent = rows[-reference_years:]
    market = round(statistics.fmean(r.eur_per_t for r in recent), 2) if recent else None
    window = [r.eur_per_t for r in rows[-VARIABILITY_YEARS:]]
    variability = None
    if len(window) >= MIN_VARIABILITY_YEARS:
        cv = statistics.pstdev(window) / statistics.fmean(window)
        variability = {
            "cv": round(cv, 3),
            "level": next(level for limit, level in VARIABILITY_LEVELS if cv < limit),
            "years": [rows[-len(window)].year, rows[-1].year],
        }
    return {
        "reference_price": override if override is not None else market,
        "price_source": "farmer" if override is not None else ("market" if market is not None else None),
        "market_reference_price": market,
        "reference_years": [recent[0].year, recent[-1].year] if recent else None,
        "confidence": min((r.confidence for r in recent), key=CONFIDENCE_ORDER.index) if recent else None,
        "variability": variability,
        "history": rows,
    }
