import logging
from typing import Literal

from fastapi import APIRouter
from pydantic import Field, model_validator

from app.routers.crops import Bounds, CamelModel, SelectedField
from app.services import strategies as svc
from app.services import strategy_economics as econ
from app.services.crop_soil_scores import hcat3_codes

log = logging.getLogger(__name__)

router = APIRouter(tags=["strategies"])


class StrategiesRequest(CamelModel):
    crops: list[str] = Field(min_length=1)
    # the land: a drawn rectangle or picked fields, same shapes as /crops
    bounds: Bounds | None = None
    fields: list[SelectedField] | None = Field(default=None, min_length=1, max_length=10_000)

    @model_validator(mode="after")
    def check_area(self) -> "StrategiesRequest":
        if (self.bounds is None) == (self.fields is None):
            raise ValueError("send exactly one of bounds or fields")
        return self

    def area_ha(self) -> float:
        if self.bounds:
            tl, br = self.bounds.top_left, self.bounds.bottom_right
            ring = [(tl.lat, tl.lng), (tl.lat, br.lng), (br.lat, br.lng), (br.lat, tl.lng)]
            return econ.polygon_area_ha(ring)
        return sum(
            econ.polygon_area_ha([(p.lat, p.lng) for p in f.polygon]) for f in self.fields or []
        )


class MonthRef(CamelModel):
    year: int  # rotation year, 1-based
    month: int  # 1-12


class Planting(CamelModel):
    crop_id: str
    crop_name: str
    role: Literal["cash", "cover", "forage"]
    start: MonthRef
    end: MonthRef
    water_use_mm: int
    traits: list[str]
    note: str


class Rating(CamelModel):
    score: int = Field(ge=1, le=5)
    summary: str


class Assessment(CamelModel):
    level: Literal["low", "medium", "high"]
    summary: str


class WaterUse(Assessment):
    by_year_mm: list[int]


class Ratings(CamelModel):
    nutrient_cycling: Rating
    organic_matter: Rating
    pest_disruption: Rating
    forage_potential: Rating
    market: Rating
    soil_improvement: Rating


class CropRoles(CamelModel):
    nitrogen_fixing: Assessment
    cover_crops: Assessment
    deep_rooting: Assessment
    drought_tolerant: Assessment


class Money(CamelModel):
    revenue: float
    seed_cost: float
    operating_cost: float
    total_cost: float
    profit: float


class PlantingMoney(CamelModel):
    crop_name: str
    role: Literal["cash", "cover", "forage"]
    revenue: float
    cost: float
    profit: float
    source: str


class YearMoney(CamelModel):
    year: int
    revenue: float
    cost: float
    profit: float


class Economics(CamelModel):
    currency: Literal["EUR"] = "EUR"
    # latest yield year of the market data used; null if none was used
    price_year: int | None
    area_ha: float
    # one pass through the rotation, per hectare
    per_ha: Money
    profit_per_ha_year: float
    # per_ha scaled to the selected area
    total: Money
    plantings: list[PlantingMoney]
    by_year: list[YearMoney]


class Strategy(CamelModel):
    id: str
    name: str
    summary: str
    duration_years: int
    matched_crops: list[str]
    plantings: list[Planting]
    ratings: Ratings
    erosion_risk: Assessment
    water_use: WaterUse
    crop_roles: CropRoles
    market_considerations: list[str]
    economics: Economics


class StrategiesResponse(CamelModel):
    strategies: list[Strategy]


def _rating(r: svc.Rating) -> Rating:
    return Rating(score=r.score, summary=r.summary)


def _assessment(a: svc.Assessment | svc.CropRole) -> Assessment:
    return Assessment(level=a.importance if isinstance(a, svc.CropRole) else a.level, summary=a.summary)


def _money(e: econ.StrategyEconomics, scale: float = 1.0) -> Money:
    return Money(
        revenue=round(e.revenue * scale),
        seed_cost=round(e.seed_cost * scale),
        operating_cost=round(e.operating_cost * scale),
        total_cost=round(e.total_cost * scale),
        profit=round(e.profit * scale),
    )


def _economics(e: econ.StrategyEconomics, area_ha: float) -> Economics:
    return Economics(
        price_year=e.price_year,
        area_ha=round(area_ha, 2),
        per_ha=_money(e),
        profit_per_ha_year=round(e.profit / e.years),
        total=_money(e, area_ha),
        plantings=[
            PlantingMoney(
                crop_name=p.planting.crop_name,
                role=p.planting.role,
                revenue=round(p.revenue),
                cost=round(p.cost),
                profit=round(p.revenue - p.cost),
                source=p.source,
            )
            for p in e.plantings
        ],
        by_year=[
            YearMoney(year=i + 1, revenue=round(r), cost=round(c), profit=round(r - c))
            for i, (r, c) in enumerate(e.by_year())
        ],
    )


def _to_model(
    s: svc.Strategy, matched: list[str], economics: Economics
) -> Strategy:
    return Strategy(
        id=s.id,
        name=s.name,
        summary=s.summary,
        duration_years=s.duration_years,
        matched_crops=matched,
        plantings=[
            Planting(
                crop_id=p.crop_id,
                crop_name=p.crop_name,
                role=p.role,
                start=MonthRef(year=p.start[0], month=p.start[1]),
                end=MonthRef(year=p.end[0], month=p.end[1]),
                water_use_mm=p.water_use_mm,
                traits=list(p.traits),
                note=p.note,
            )
            for p in s.plantings
        ],
        ratings=Ratings(
            nutrient_cycling=_rating(s.nutrient_cycling),
            organic_matter=_rating(s.organic_matter),
            pest_disruption=_rating(s.pest_disruption),
            forage_potential=_rating(s.forage_potential),
            market=_rating(s.market),
            soil_improvement=_rating(s.soil_improvement),
        ),
        erosion_risk=_assessment(s.erosion_risk),
        water_use=WaterUse(
            level=s.water_use.level,
            summary=s.water_use.summary,
            by_year_mm=s.water_by_year(),
        ),
        crop_roles=CropRoles(
            nitrogen_fixing=_assessment(s.nitrogen_fixing),
            cover_crops=_assessment(s.cover_crops),
            deep_rooting=_assessment(s.deep_rooting),
            drought_tolerant=_assessment(s.drought_tolerant),
        ),
        market_considerations=list(s.market_considerations),
        economics=economics,
    )


@router.post(
    "/strategies",
    response_model=StrategiesResponse,
    response_model_by_alias=True,
    summary="Multi-year crop rotation strategies for an area",
)
def strategies(request: StrategiesRequest) -> StrategiesResponse:
    # TODO: use the area (soil, climate) instead of hard-coded plans
    area_ha = request.area_ha()
    try:
        codes = hcat3_codes(request.crops)
    except Exception:
        # still matches the plans' own crop ids
        log.exception("could not look up HCAT codes")
        codes = {}

    return StrategiesResponse(
        strategies=[
            _to_model(
                s,
                matched,
                _economics(econ.strategy_economics(s), area_ha),
            )
            for s, matched in svc.strategies_for(request.crops, codes)
        ]
    )
