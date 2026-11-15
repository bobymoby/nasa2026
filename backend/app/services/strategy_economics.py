"""Revenue of a rotation strategy.

Revenue of a crop is the country's reference yield times its reference price
(`prices.market_reference`: national 5-year means from Eurostat, FAOSTAT where
Eurostat has none; prices in real euros of the price year). There is no cost
data, so no cost or profit is computed. Only crops with a yield, a price and
their variability are plannable (`plannable`).

Money is attributed to the harvest year; a forage ley earns every season.
"""

import math
from dataclasses import dataclass

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
    price_year: int

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


def _planting_revenue(p: dict, market: Market) -> PlantingRevenue:
    crop_id, role, name = p["crop_id"], p["role"], p["crop_name"]
    start_year, end_year = p["start"]["year"], p["end"]["year"]
    m = market.crops[crop_id]
    revenue = crop_revenue_eur_ha(crop_id, market)
    seasons = list(range(start_year, end_year + 1)) if role == "forage" else [end_year]
    return PlantingRevenue(
        crop_id, name, role, revenue * len(seasons), m.yield_t_ha, m.price_eur_t, _basis(m),
        [(year, revenue) for year in seasons],
    )


def strategy_revenue(plantings: list[dict], years: int, market: Market) -> StrategyRevenue:
    """Per-hectare revenue of one pass through a generated rotation."""
    return StrategyRevenue(
        plantings=[_planting_revenue(p, market) for p in plantings],
        years=years,
        country=market.country,
        price_year=market.price_year,
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
