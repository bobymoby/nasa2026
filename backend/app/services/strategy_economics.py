"""Revenue of a rotation strategy.

Revenue of a harvested crop is the country's reference yield times its
reference price (`prices.market_reference`: national 5-year means from
Eurostat, FAOSTAT where Eurostat has none; prices in real euros of the price
year). There is no sourced cost data, so no cost or profit is computed.

A crop missing its market figures is left out of the totals and marks the
strategy incomplete; nothing is filled in by hand. Cover crops bring no revenue.

Money is attributed to the harvest year; a forage ley earns every season.

EU CAP payments (`cap_payments`) are kept apart from the market revenue.
"""

import math
from dataclasses import dataclass, field

from app.services import cap_payments
from app.services.prices.pipeline import CropMarket, Market

NO_MARKET_DATA = "no market data"

# strategy crop id -> price table crop id
MARKET_IDS = {"field_pea": "dry_pea"}


@dataclass(frozen=True)
class PlantingEconomics:
    crop_id: str
    crop_name: str
    role: str
    revenue: float
    yield_t_ha: float | None
    price_eur_t: float | None
    # where yield and price came from, "no market data" or "no harvest"
    source: str
    # (rotation year, revenue) slices of this planting
    by_year: list[tuple[int, float]]

    @property
    def counted(self) -> bool:
        return self.source != NO_MARKET_DATA


@dataclass(frozen=True)
class StrategyEconomics:
    plantings: list[PlantingEconomics]
    years: int
    country: str
    price_year: int | None
    cap: list[cap_payments.CapLine] = field(default_factory=list)

    @property
    def cap_total(self) -> float:
        return sum(line.eur_per_ha for line in self.cap)

    @property
    def revenue(self) -> float:
        return sum(p.revenue for p in self.plantings)

    @property
    def revenue_with_cap(self) -> float:
        return self.revenue + self.cap_total

    @property
    def missing_data(self) -> list[str]:
        return list(dict.fromkeys(p.crop_name for p in self.plantings if not p.counted))

    @property
    def complete(self) -> bool:
        return not self.missing_data

    def by_year(self) -> list[float]:
        """Revenue per rotation year."""
        totals = [0.0] * self.years
        for p in self.plantings:
            for year, revenue in p.by_year:
                totals[year - 1] += revenue
        return totals


def market_id(crop_id: str) -> str:
    return MARKET_IDS.get(crop_id, crop_id)


def _years(span: tuple[int, int]) -> str:
    return str(span[0]) if span[0] == span[1] else f"{span[0]}–{span[1]}"


def _basis(m: CropMarket) -> str:
    return (
        f"price {m.price_source} {_years(m.price_years)}, "
        f"yield {m.yield_source} {_years(m.yield_years)}"
    )


def _season(crop_id: str, market: Market) -> tuple[float | None, float | None, float, str]:
    """(yield, price, revenue, basis) of one harvest season per ha."""
    m = market.crops.get(market_id(crop_id))
    if m is None:
        return None, None, 0.0, NO_MARKET_DATA
    if m.price_eur_t is None or m.yield_t_ha is None:
        return m.yield_t_ha, m.price_eur_t, 0.0, NO_MARKET_DATA
    return m.yield_t_ha, m.price_eur_t, m.yield_t_ha * m.price_eur_t, _basis(m)


def plannable(crop_id: str, market: Market) -> bool:
    """A harvested crop with a national yield and price."""
    return _season(crop_id, market)[3] != NO_MARKET_DATA


def crop_revenue_eur_ha(crop_id: str, market: Market) -> float:
    """Revenue of one season of a harvested crop, 0 without market figures."""
    return _season(crop_id, market)[2]


def _planting_economics(p: dict, market: Market) -> PlantingEconomics:
    crop_id, role, name = p["crop_id"], p["role"], p["crop_name"]
    start_year, end_year = p["start"]["year"], p["end"]["year"]
    if role == "cover":
        return PlantingEconomics(crop_id, name, role, 0.0, None, None, "no harvest", [(start_year, 0.0)])

    yield_t_ha, price, revenue, basis = _season(crop_id, market)
    seasons = list(range(start_year, end_year + 1)) if role == "forage" else [end_year]
    return PlantingEconomics(
        crop_id, name, role, revenue * len(seasons), yield_t_ha, price, basis,
        [(year, revenue) for year in seasons],
    )


def strategy_economics(
    plantings: list[dict], years: int, market: Market, area_ha: float = 0.0,
) -> StrategyEconomics:
    """Per-hectare revenue of one pass through a generated rotation."""
    return StrategyEconomics(
        plantings=[_planting_economics(p, market) for p in plantings],
        years=years,
        country=market.country,
        price_year=market.price_year,
        cap=cap_payments.cap_lines(plantings, years, area_ha),
    )


def polygon_area_ha(ring: list[tuple[float, float]]) -> float:
    """Area of a (lat, lng) ring, on a local flat projection; fine for fields and small areas."""
    if len(ring) < 3:
        return 0.0
    lat0 = math.radians(sum(lat for lat, _ in ring) / len(ring))
    kx = 111_320.0 * math.cos(lat0)
    ky = 110_540.0
    pts = [(lng * kx, lat * ky) for lat, lng in ring]
    twice_area = sum(
        x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1])
    )
    return abs(twice_area) / 2 / 10_000
