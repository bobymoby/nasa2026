"""Estimated cost and profit of a rotation strategy.

Revenue of a harvested crop is the country's reference yield times its
reference price (`prices.market_reference`: national 5-year means from
Eurostat, FAOSTAT where Eurostat has none; prices in real euros of the price
year).

Costs are still hard-coded estimates for Bulgarian conditions, one entry per
harvested crop and no catch-all default:
- seed, and other operating costs (fertiliser, crop protection, machinery and fuel)
- cover crops (seed + sowing and termination; no harvest)

A crop missing its market figures or its cost estimate is left out of the
totals and marks the strategy incomplete; nothing is filled in by hand.

Money is attributed to the harvest year; a forage ley is split per season.
"""

import math
from dataclasses import dataclass

from app.services.prices.pipeline import CropMarket, Market

NO_MARKET_DATA = "no market data"
NO_COST_DATA = "no cost estimate"

# strategy crop id -> price table crop id
MARKET_IDS = {"field_pea": "dry_pea"}

# €/ha per season
SEED_COSTS_EUR_HA = {
    "wheat": 115.0,
    "barley": 90.0,
    "rye": 72.0,
    "oat": 70.0,
    "maize": 169.0,
    "sorghum": 60.0,
    "buckwheat": 60.0,
    "sunflower": 72.0,
    "rapeseed": 60.0,
    "flax": 75.0,
    "soybean": 143.0,
    "chickpea": 132.0,
    "field_pea": 150.0,
    "alfalfa": 120.0,
    "carrot": 700.0,
    "reed_canary_grass": 90.0,
}

# €/ha per season, excluding seed
OPERATING_COSTS_EUR_HA = {
    "wheat": 480.0,
    "barley": 430.0,
    "maize": 550.0,
    "sunflower": 400.0,
    "rapeseed": 550.0,
    "soybean": 320.0,
    "chickpea": 290.0,
    "field_pea": 330.0,
    "oat": 380.0,
    "rye": 380.0,
    "sorghum": 420.0,
    "buckwheat": 330.0,
    "flax": 380.0,
    "alfalfa": 340.0,
    "reed_canary_grass": 300.0,
    # irrigation, hand weeding, harvest and washing
    "carrot": 4800.0,
}

# cover crops: (seed, sowing + termination) in €/ha
COVER_COSTS_EUR_HA = {
    "vetch_oats": (70.0, 60.0),
    "phacelia": (45.0, 50.0),
    "mustard": (30.0, 50.0),
    "cereal_rye": (60.0, 60.0),
    "pea_oats": (80.0, 60.0),
    "clover_radish": (65.0, 60.0),
}
DEFAULT_COVER_COST_EUR_HA = (60.0, 60.0)


@dataclass(frozen=True)
class PlantingEconomics:
    crop_id: str
    crop_name: str
    role: str
    revenue: float
    seed_cost: float
    operating_cost: float
    yield_t_ha: float | None
    price_eur_t: float | None
    # where yield and price came from, "no market data" or "no harvest"
    source: str
    # (rotation year, revenue, cost) slices of this planting
    by_year: list[tuple[int, float, float]]

    @property
    def cost(self) -> float:
        return self.seed_cost + self.operating_cost

    @property
    def counted(self) -> bool:
        return self.source not in (NO_MARKET_DATA, NO_COST_DATA)


@dataclass(frozen=True)
class StrategyEconomics:
    plantings: list[PlantingEconomics]
    years: int
    country: str
    price_year: int | None

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

    @property
    def missing_data(self) -> list[str]:
        return list(dict.fromkeys(p.crop_name for p in self.plantings if not p.counted))

    @property
    def complete(self) -> bool:
        return not self.missing_data

    def by_year(self) -> list[tuple[float, float]]:
        """(revenue, cost) per rotation year."""
        totals = [[0.0, 0.0] for _ in range(self.years)]
        for p in self.plantings:
            for year, revenue, cost in p.by_year:
                totals[year - 1][0] += revenue
                totals[year - 1][1] += cost
        return [(r, c) for r, c in totals]


def market_id(crop_id: str) -> str:
    return MARKET_IDS.get(crop_id, crop_id)


def _years(span: tuple[int, int]) -> str:
    return str(span[0]) if span[0] == span[1] else f"{span[0]}–{span[1]}"


def _basis(m: CropMarket) -> str:
    return (
        f"price {m.price_source} {_years(m.price_years)}, "
        f"yield {m.yield_source} {_years(m.yield_years)}"
    )


def _costs(crop_id: str) -> tuple[float, float] | None:
    if crop_id not in SEED_COSTS_EUR_HA or crop_id not in OPERATING_COSTS_EUR_HA:
        return None
    return SEED_COSTS_EUR_HA[crop_id], OPERATING_COSTS_EUR_HA[crop_id]


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
    crop_id, role, name = p["crop_id"], p["role"], p["crop_name"]
    start_year, end_year = p["start"]["year"], p["end"]["year"]

    if role == "cover":
        seed, ops = COVER_COSTS_EUR_HA.get(crop_id, DEFAULT_COVER_COST_EUR_HA)
        return PlantingEconomics(
            crop_id, name, role, 0.0, seed, ops, None, None, "no harvest", [(start_year, 0.0, seed + ops)]
        )

    yield_t_ha, price, revenue, basis = _season(crop_id, market)
    costs = _costs(crop_id)
    if costs is None or basis == NO_MARKET_DATA:
        return PlantingEconomics(
            crop_id, name, role, 0.0, 0.0, 0.0, yield_t_ha, price,
            NO_COST_DATA if costs is None else NO_MARKET_DATA, [(end_year, 0.0, 0.0)],
        )
    seed, ops = costs

    if role == "forage":
        seasons = list(range(start_year, end_year + 1))
        by_year = [(y, revenue, ops + (seed if y == start_year else 0.0)) for y in seasons]
        return PlantingEconomics(
            crop_id, name, role, revenue * len(seasons), seed, ops * len(seasons),
            yield_t_ha, price, basis, by_year,
        )

    return PlantingEconomics(
        crop_id, name, role, revenue, seed, ops, yield_t_ha, price, basis,
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
