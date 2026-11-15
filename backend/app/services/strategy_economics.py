"""Estimated cost and profit of a rotation strategy.

Revenue of a crop is the country's reference yield times its reference price
(`prices.market_reference`: national 5-year means from Eurostat, FAOSTAT where
Eurostat has none; prices in real euros of the price year). Only crops with
both figures and a cost entry are plannable (`plannable`).

Costs are hard-coded estimates for Bulgarian conditions (€/ha per season):
seed, and other operating costs (fertiliser, crop protection, machinery and
fuel).

Money is attributed to the harvest year; a forage ley is split per season.
"""

import math
from dataclasses import dataclass

from app.services.prices.pipeline import CropMarket, Market

SEED_COSTS_EUR_HA = {
    "wheat": 115.0,
    "barley": 90.0,
    "rye": 72.0,
    "oat": 70.0,
    "maize": 169.0,
    "sorghum": 60.0,
    "buckwheat": 60.0,
    "foxtail_millet": 25.0,
    "sunflower": 72.0,
    "rapeseed": 60.0,
    "flax": 75.0,
    "soybean": 143.0,
    "chickpea": 132.0,
    "dry_pea": 150.0,
    "phaseolus_bean": 180.0,
    "sugarbeet": 250.0,
    "white_potato": 1800.0,
    "tobacco": 150.0,
    "cotton": 120.0,
    "alfalfa": 120.0,
    "carrot": 700.0,
    "cabbage": 500.0,
    "onion": 600.0,
    "tomato": 900.0,
}

OPERATING_COSTS_EUR_HA = {
    "wheat": 480.0,
    "barley": 430.0,
    "rye": 380.0,
    "oat": 380.0,
    "maize": 550.0,
    "sorghum": 420.0,
    "buckwheat": 330.0,
    "foxtail_millet": 300.0,
    "sunflower": 400.0,
    "rapeseed": 550.0,
    "flax": 380.0,
    "soybean": 320.0,
    "chickpea": 290.0,
    "dry_pea": 330.0,
    "phaseolus_bean": 450.0,
    "sugarbeet": 1100.0,
    "white_potato": 2200.0,
    "tobacco": 2500.0,
    "cotton": 600.0,
    "alfalfa": 340.0,
    # vegetables: irrigation, hand weeding, harvest and grading
    "carrot": 4800.0,
    "cabbage": 3500.0,
    "onion": 3000.0,
    "tomato": 5500.0,
}


@dataclass(frozen=True)
class PlantingEconomics:
    crop_id: str
    crop_name: str
    role: str
    revenue: float
    seed_cost: float
    operating_cost: float
    yield_t_ha: float
    price_eur_t: float
    # where yield and price came from
    source: str
    # (rotation year, revenue, cost) slices of this planting
    by_year: list[tuple[int, float, float]]

    @property
    def cost(self) -> float:
        return self.seed_cost + self.operating_cost


@dataclass(frozen=True)
class StrategyEconomics:
    plantings: list[PlantingEconomics]
    years: int
    country: str
    price_year: int

    @property
    def revenue(self) -> float:
        return sum(p.revenue for p in self.plantings)

    @property
    def seed_cost(self) -> float:
        return sum(p.seed_cost for p in self.plantings)

    @property
    def operating_cost(self) -> float:
        return sum(p.operating_cost for p in self.plantings)

    @property
    def total_cost(self) -> float:
        return self.seed_cost + self.operating_cost

    @property
    def profit(self) -> float:
        return self.revenue - self.total_cost

    def by_year(self) -> list[tuple[float, float]]:
        """(revenue, cost) per rotation year."""
        totals = [[0.0, 0.0] for _ in range(self.years)]
        for p in self.plantings:
            for year, revenue, cost in p.by_year:
                totals[year - 1][0] += revenue
                totals[year - 1][1] += cost
        return [(r, c) for r, c in totals]


def plannable(crop_id: str, market: Market) -> bool:
    m = market.crops.get(crop_id)
    return (
        crop_id in SEED_COSTS_EUR_HA
        and crop_id in OPERATING_COSTS_EUR_HA
        and m is not None
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


def crop_profit_eur_ha(crop_id: str, market: Market) -> float:
    """Profit of one season, as the economics below counts it."""
    m = market.crops[crop_id]
    return m.yield_t_ha * m.price_eur_t - SEED_COSTS_EUR_HA[crop_id] - OPERATING_COSTS_EUR_HA[crop_id]


def _planting_economics(p: dict, market: Market) -> PlantingEconomics:
    crop_id, role, name = p["crop_id"], p["role"], p["crop_name"]
    start_year, end_year = p["start"]["year"], p["end"]["year"]
    m = market.crops[crop_id]
    revenue = m.yield_t_ha * m.price_eur_t
    seed, ops = SEED_COSTS_EUR_HA[crop_id], OPERATING_COSTS_EUR_HA[crop_id]

    if role == "forage":
        seasons = list(range(start_year, end_year + 1))
        by_year = [(y, revenue, ops + (seed if y == start_year else 0.0)) for y in seasons]
        return PlantingEconomics(
            crop_id, name, role, revenue * len(seasons), seed, ops * len(seasons),
            m.yield_t_ha, m.price_eur_t, _basis(m), by_year,
        )

    return PlantingEconomics(
        crop_id, name, role, revenue, seed, ops, m.yield_t_ha, m.price_eur_t, _basis(m),
        [(end_year, revenue, seed + ops)],
    )


def strategy_economics(plantings: list[dict], years: int, market: Market) -> StrategyEconomics:
    """Per-hectare economics of one pass through a generated rotation."""
    return StrategyEconomics(
        plantings=[_planting_economics(p, market) for p in plantings],
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
