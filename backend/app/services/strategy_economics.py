"""Estimated cost and profit of a rotation strategy.

Yield, price and seed cost of cash crops are 2026 Bulgarian market figures
(from luben data/crop_economics_bg.csv). Everything else is a hard-coded
estimate for Bulgarian conditions:
- other operating costs (fertiliser, crop protection, machinery and fuel)
- cover crops (seed + sowing and termination; no harvest)
- alfalfa hay, which has no market figures

Money is attributed to the harvest year; a forage ley is split per season.
"""

import logging
import math
from dataclasses import dataclass

from app.services.strategies import Planting, Strategy

log = logging.getLogger(__name__)

# €/ha per season, excluding seed
OPERATING_COSTS_EUR_HA = {
    "wheat": 480.0,
    "barley": 430.0,
    "maize": 550.0,
    "sunflower": 400.0,
    "rapeseed": 550.0,
    "soybean": 320.0,
    "chickpea": 290.0,
    "alfalfa": 340.0,
}
DEFAULT_OPERATING_COST_EUR_HA = 450.0

# cover crops: (seed, sowing + termination) in €/ha
COVER_COSTS_EUR_HA = {
    "vetch_oats": (70.0, 60.0),
    "phacelia": (45.0, 50.0),
    "mustard": (30.0, 50.0),
    "rye": (60.0, 60.0),
    "pea_oats": (80.0, 60.0),
    "clover_radish": (65.0, 60.0),
}
DEFAULT_COVER_COST_EUR_HA = (60.0, 60.0)


@dataclass(frozen=True)
class CropPrice:
    yield_t_ha: float
    price_eur_t: float
    seed_cost_eur_ha: float
    # yield year of the market figures; None = hard-coded estimate
    year: int | None


CROP_PRICES = {
    "potato": CropPrice(16.44, 350.0, 1800.0, 2026),
    "rapeseed": CropPrice(3.21, 479.0, 60.0, 2026),
    "sunflower": CropPrice(2.59, 489.0, 72.0, 2026),
    "wheat": CropPrice(6.33, 189.0, 115.0, 2026),
    "barley": CropPrice(5.88, 178.0, 90.0, 2026),
    "maize": CropPrice(5.65, 196.0, 169.0, 2026),
    "soybean": CropPrice(2.2, 430.0, 143.0, 2026),
    "chickpea": CropPrice(1.5, 600.0, 132.0, 2026),
    "lentil": CropPrice(1.3, 600.0, 84.0, 2026),
    "triticale": CropPrice(4.15, 170.0, 84.0, 2026),
    "pea": CropPrice(2.5, 280.0, 150.0, 2026),
    "rye": CropPrice(2.5, 165.0, 72.0, 2026),
    # hay per harvest season; seed is paid once when the ley is sown
    "alfalfa": CropPrice(7.0, 140.0, 120.0, None),
}


@dataclass(frozen=True)
class PlantingEconomics:
    planting: Planting
    revenue: float
    seed_cost: float
    operating_cost: float
    # where yield and price came from: "market <year>", "estimate" or "no harvest"
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

    def by_year(self) -> list[tuple[float, float]]:
        """(revenue, cost) per rotation year."""
        totals = [[0.0, 0.0] for _ in range(self.years)]
        for p in self.plantings:
            for year, revenue, cost in p.by_year:
                totals[year - 1][0] += revenue
                totals[year - 1][1] += cost
        return [(r, c) for r, c in totals]


def _planting_economics(p: Planting) -> PlantingEconomics:
    start_year, end_year = p.start[0], p.end[0]

    if p.role == "cover":
        seed, ops = COVER_COSTS_EUR_HA.get(p.crop_id, DEFAULT_COVER_COST_EUR_HA)
        return PlantingEconomics(p, 0.0, seed, ops, "no harvest", [(start_year, 0.0, seed + ops)])

    price = CROP_PRICES.get(p.crop_id)
    ops_per_season = OPERATING_COSTS_EUR_HA.get(p.crop_id, DEFAULT_OPERATING_COST_EUR_HA)
    if price is None:
        log.warning("no price for %s; counting costs only", p.crop_id)
        return PlantingEconomics(
            p, 0.0, 0.0, ops_per_season, "no price data", [(end_year, 0.0, ops_per_season)]
        )

    source = f"market {price.year}" if price.year else "estimate"
    revenue_per_season = price.yield_t_ha * price.price_eur_t

    if p.role == "forage":
        # a ley is cut every year it stands
        seasons = list(range(start_year, end_year + 1))
        by_year = [
            (y, revenue_per_season, ops_per_season + (price.seed_cost_eur_ha if y == start_year else 0.0))
            for y in seasons
        ]
        return PlantingEconomics(
            p,
            revenue_per_season * len(seasons),
            price.seed_cost_eur_ha,
            ops_per_season * len(seasons),
            source,
            by_year,
        )

    return PlantingEconomics(
        p,
        revenue_per_season,
        price.seed_cost_eur_ha,
        ops_per_season,
        source,
        [(end_year, revenue_per_season, price.seed_cost_eur_ha + ops_per_season)],
    )


def strategy_economics(strategy: Strategy) -> StrategyEconomics:
    """Per-hectare economics of one pass through the rotation."""
    plantings = [_planting_economics(p) for p in strategy.plantings]
    years = [
        price.year
        for p in strategy.plantings
        if p.role != "cover" and (price := CROP_PRICES.get(p.crop_id)) and price.year
    ]
    return StrategyEconomics(
        plantings=plantings,
        years=strategy.duration_years,
        price_year=max(years) if years else None,
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
