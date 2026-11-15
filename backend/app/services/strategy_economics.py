"""Revenue of a rotation strategy.

Revenue of a crop is the country's reference yield times its reference price
(`prices.market_reference`: national 5-year means from Eurostat, FAOSTAT where
Eurostat has none; prices in real euros of the price year). There is no cost
data, so no cost or profit is computed. Only crops with a yield, a price and
their variability are plannable (`plannable`).

<<<<<<< HEAD
Money is attributed to the harvest year; a forage ley earns every season.
=======
Costs are still hard-coded estimates for Bulgarian conditions, one entry per
harvested crop and no catch-all default:
- seed, and other operating costs (fertiliser, crop protection, machinery and fuel)
- cover crops (seed + sowing and termination; no harvest)

A crop missing its market figures or its cost estimate is left out of the
totals and marks the strategy incomplete; nothing is filled in by hand.

Money is attributed to the harvest year; a forage ley is split per season.

EU CAP payments (`cap_payments`) are kept apart from the market profit, so
`profit` is what the crops earn and `profit_with_cap` adds the payments.
>>>>>>> origin/main
"""

import math
from dataclasses import dataclass, field

from app.services import cap_payments
from app.services.prices.pipeline import CropMarket, Market


@dataclass(frozen=True)
class PlantingRevenue:
    crop_id: str
    crop_name: str
    role: str
    revenue: float
    yield_t_ha: float
    price_eur_t: float
    # where yield and price came from
    source: str
    # (rotation year, revenue) slices of this planting
    by_year: list[tuple[int, float]]


@dataclass(frozen=True)
class StrategyRevenue:
    plantings: list[PlantingRevenue]
    years: int
    country: str
<<<<<<< HEAD
    price_year: int
=======
    price_year: int | None
    cap: list[cap_payments.CapLine] = field(default_factory=list)

    @property
    def cap_total(self) -> float:
        return sum(line.eur_per_ha for line in self.cap)

    @property
    def profit_with_cap(self) -> float:
        return self.profit + self.cap_total
>>>>>>> origin/main

    @property
    def revenue(self) -> float:
        return sum(p.revenue for p in self.plantings)

    def by_year(self) -> list[float]:
        totals = [0.0] * self.years
        for p in self.plantings:
            for year, revenue in p.by_year:
                totals[year - 1] += revenue
        return totals


def plannable(crop_id: str, market: Market) -> bool:
    m = market.crops.get(crop_id)
    return (
        m is not None
        and m.price_eur_t is not None
        and m.yield_t_ha is not None
        and m.price_cv is not None
        and m.yield_cv is not None
    )


def _years(span: tuple[int, int]) -> str:
    return str(span[0]) if span[0] == span[1] else f"{span[0]}–{span[1]}"


def _basis(m: CropMarket) -> str:
    return (
        f"price {m.price_source} {_years(m.price_years)}, "
        f"yield {m.yield_source} {_years(m.yield_years)}"
    )


def crop_revenue_eur_ha(crop_id: str, market: Market) -> float:
    m = market.crops[crop_id]
    return m.yield_t_ha * m.price_eur_t


<<<<<<< HEAD
def _planting_revenue(p: dict, market: Market) -> PlantingRevenue:
=======
def _season(crop_id: str, market: Market) -> tuple[float | None, float | None, float, str]:
    """(yield, price, revenue, basis) of one harvest season per ha."""
    m = market.crops.get(market_id(crop_id))
    if m is None:
        return None, None, 0.0, NO_MARKET_DATA
    if m.price_eur_t is None or m.yield_t_ha is None:
        return m.yield_t_ha, m.price_eur_t, 0.0, NO_MARKET_DATA
    return m.yield_t_ha, m.price_eur_t, m.yield_t_ha * m.price_eur_t, _basis(m)


def plannable(crop_id: str, market: Market) -> bool:
    """A harvested crop whose revenue and costs can both be counted."""
    return _costs(crop_id) is not None and _season(crop_id, market)[3] != NO_MARKET_DATA


def crop_profit_eur_ha(crop_id: str, market: Market) -> float:
    """Profit of one season of a harvested crop, as the economics below counts it."""
    costs = _costs(crop_id)
    _, _, revenue, basis = _season(crop_id, market)
    if costs is None or basis == NO_MARKET_DATA:
        return 0.0
    return revenue - sum(costs)


def _planting_economics(p: dict, market: Market) -> PlantingEconomics:
>>>>>>> 686549bf26d1e76f75b702537984176bd6dfbac6
    crop_id, role, name = p["crop_id"], p["role"], p["crop_name"]
    start_year, end_year = p["start"]["year"], p["end"]["year"]
    m = market.crops[crop_id]
    revenue = crop_revenue_eur_ha(crop_id, market)
    seasons = list(range(start_year, end_year + 1)) if role == "forage" else [end_year]
    return PlantingRevenue(
        crop_id, name, role, revenue * len(seasons), m.yield_t_ha, m.price_eur_t, _basis(m),
        [(year, revenue) for year in seasons],
    )


<<<<<<< HEAD
def strategy_revenue(plantings: list[dict], years: int, market: Market) -> StrategyRevenue:
    """Per-hectare revenue of one pass through a generated rotation."""
    return StrategyRevenue(
        plantings=[_planting_revenue(p, market) for p in plantings],
=======
def strategy_economics(
    plantings: list[dict], years: int, market: Market, area_ha: float = 0.0,
) -> StrategyEconomics:
    """Per-hectare economics of one pass through a generated rotation."""
    return StrategyEconomics(
        plantings=[_planting_economics(p, market) for p in plantings],
>>>>>>> origin/main
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
