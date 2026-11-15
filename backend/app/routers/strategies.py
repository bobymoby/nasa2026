import logging
from itertools import permutations
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.routers.crops import Bounds, CamelModel, SelectedField
from app.services import crop_calendar
from app.services import strategies as svc
from app.services import strategy_economics as econ
from app.services.country import country_at
from app.services.prices.pipeline import Market, market_reference

log = logging.getLogger(__name__)

router = APIRouter(tags=["strategies"])

WeightedStrategyType = Literal[
    "revenue_first", "soil_builder", "risk_balanced", "water_efficient", "balanced"
]
StrategyType = Literal[
    "current", "revenue_first", "soil_builder", "risk_balanced", "water_efficient", "balanced"
]


class StrategyWeights(CamelModel):
    model_config = ConfigDict(extra="forbid")

    revenue: float | None = Field(default=None, ge=0)
    soil_benefit: float | None = Field(default=None, ge=0)
    risk_control: float | None = Field(default=None, ge=0)
    water_efficiency: float | None = Field(default=None, ge=0)
    feasibility: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def has_positive_weight(self) -> "StrategyWeights":
        if not any(value and value > 0 for value in self.model_dump().values()):
            raise ValueError("each scoring profile must include a positive weight")
        return self


class AreaRequest(CamelModel):
    bounds: Bounds | None = None
    fields: list[SelectedField] | None = Field(default=None, min_length=1, max_length=10_000)

    @model_validator(mode="after")
    def one_area(self) -> "AreaRequest":
        if (self.bounds is None) == (self.fields is None):
            raise ValueError("send exactly one of bounds or fields")
        return self

    def centre(self) -> tuple[float, float]:
        if self.bounds:
            tl, br = self.bounds.top_left, self.bounds.bottom_right
            return (tl.lat + br.lat) / 2, (tl.lng + br.lng) / 2
        ring = self.fields[0].polygon
        return sum(p.lat for p in ring) / len(ring), sum(p.lng for p in ring) / len(ring)

    def area_ha(self) -> float:
        if self.bounds:
            tl, br = self.bounds.top_left, self.bounds.bottom_right
            ring = [(tl.lat, tl.lng), (tl.lat, br.lng), (br.lat, br.lng), (br.lat, tl.lng)]
            return econ.polygon_area_ha(ring)
        return sum(
            econ.polygon_area_ha([(p.lat, p.lng) for p in f.polygon]) for f in self.fields or []
        )


class StrategiesRequest(AreaRequest):
    crops: list[str] = Field(default_factory=list, max_length=50)
    current_crops: list[str] = Field(
        default_factory=list,
        max_length=8,
        description="Crops grown now, in planting order; returned first as the current rotation.",
    )
    years: int = Field(default=3, ge=2, le=5)
    available_crops: list[str] | None = Field(default=None, min_length=1, max_length=8)
    previously_planted: list[str] = Field(default_factory=list, max_length=20)
    preferred_crops: list[str] = Field(default_factory=list, max_length=8)
    excluded_crops: list[str] = Field(default_factory=list, max_length=8)
    crop_suitability: dict[str, float] = Field(
        default_factory=dict,
        description="Optional crop suitability overrides on a 0-100 scale.",
    )
    irrigation_available: bool = False
    baseline_rotation: list[str] | None = Field(default=None, min_length=2, max_length=5)
    strategy_weights: dict[WeightedStrategyType, StrategyWeights] | None = None
    site_context: dict[str, Any]

    @model_validator(mode="after")
    def validate_request(self) -> "StrategiesRequest":
        if not (self.crops or self.available_crops):
            raise ValueError("select at least one crop using crops or availableCrops")
        if any(not 0 <= score <= 100 for score in self.crop_suitability.values()):
            raise ValueError("cropSuitability scores must be between 0 and 100")
        if set(self.excluded_crops) & set(self.preferred_crops):
            raise ValueError("a crop cannot be both preferred and excluded")
        if set(self.crops) & set(self.excluded_crops):
            raise ValueError("a crop cannot be both selected and excluded")
        if self.baseline_rotation and len(self.baseline_rotation) != self.years:
            raise ValueError("baselineRotation must contain one crop per rotation year")
        return self


class MonthRef(CamelModel):
    year: int
    month: int


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
    level: Literal["low", "medium", "high", "unknown"]
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


class Evaluation(CamelModel):
    revenue_eur_ha: float
    soil_benefit_score: float
    risk_score: float
    water_demand_mm: int
    water_efficiency_score: float
    agronomic_feasibility_score: float
    feasible: bool
    suitability_scores: list[float]
    preferred_crop_coverage: float
    rotation_constraint_penalty: float
    score_components: dict[str, float]
    scoring_weights: dict[str, float]


class StrategyComparison(BaseModel):
    revenue_difference_eur_ha: float
    risk_difference: float
    soil_benefit_difference: float
    water_demand_difference: int


class PlantingRevenue(CamelModel):
    crop_name: str
    role: Literal["cash", "cover", "forage"]
    yield_t_ha: float
    price_eur_t: float
    revenue: float
    source: str


class YearRevenue(CamelModel):
    year: int
    revenue: float


class Economics(CamelModel):
    currency: Literal["EUR"] = "EUR"
    country: str
    price_year: int
    area_ha: float
    revenue_per_ha: float
    revenue_per_ha_year: float
    total_revenue: float
    plantings: list[PlantingRevenue]
    by_year: list[YearRevenue]


class Strategy(CamelModel):
    id: str
    name: str
    summary: str
    duration_years: int
    matched_crops: list[str]
    rotation: list[str]
    strategy_type: StrategyType
    ranking: int
    score: float
    plantings: list[Planting]
    ratings: Ratings
    erosion_risk: Assessment
    water_use: WaterUse
    crop_roles: CropRoles
    market_considerations: list[str]
    evaluation: Evaluation
    comparison: StrategyComparison
    explanations: list[str]
    economics: Economics


class StrategiesResponse(CamelModel):
    rotations_evaluated: int
    baseline_rotation: list[str]
    baseline: dict[str, object]
    assumptions: list[str]
    strategies: list[Strategy]


class PlannableCrops(CamelModel):
    country: str
    crops: list[str]


def _market(request: AreaRequest) -> Market:
    country = country_at(*request.centre())
    if country is None:
        raise HTTPException(422, "The selected area is not inside a supported country.")
    return market_reference(country, list(svc.CROP_BY_ID))


def _plannable(market: Market) -> list[str]:
    return [c for c in svc.CROP_BY_ID if econ.plannable(c, market)]


def _economics(strategy: dict, area_ha: float, market: Market) -> Economics:
    e = econ.strategy_revenue(strategy["plantings"], strategy["duration_years"], market)
    return Economics(
        country=e.country,
        price_year=e.price_year,
        area_ha=round(area_ha, 2),
        revenue_per_ha=round(e.revenue),
        revenue_per_ha_year=round(e.revenue / e.years),
        total_revenue=round(e.revenue * area_ha),
        plantings=[
            PlantingRevenue(
                crop_name=p.crop_name,
                role=p.role,
                yield_t_ha=p.yield_t_ha,
                price_eur_t=p.price_eur_t,
                revenue=round(p.revenue),
                source=p.source,
            )
            for p in e.plantings
        ],
        by_year=[YearRevenue(year=i + 1, revenue=round(r)) for i, r in enumerate(e.by_year())],
    )


def _default_baseline(pool: list[str], years: int, calendar: svc.agronomy.Calendar) -> list[str]:
    """The regional baseline crops in the first order that fits this area's calendar."""
    crops = [c for c in svc.DEFAULT_BASELINE if c in pool]
    for order in permutations(crops):
        sequence = [order[i % len(order)] for i in range(years)]
        if svc.schedule_fits(sequence, calendar):
            return sequence
    raise HTTPException(422, "The regional baseline rotation does not fit this area's crop calendar.")


def _suitability(site_context: dict[str, Any]) -> dict[str, float]:
    """Mean GAEZ suitability per crop over the plots."""
    values: dict[str, list[float]] = {}
    for plot in site_context.get("plots") or []:
        for crop_id, data in (plot.get("crops") or {}).items():
            values.setdefault(crop_id, []).append(float(data["suitability"]))
    return {crop_id: sum(v) / len(v) for crop_id, v in values.items()}


def _climate(site_context: dict[str, Any]) -> tuple[dict[int, float], svc.agronomy.Frost]:
    climate = site_context.get("climate")
    if not climate:
        raise HTTPException(
            503, "Climate normals for this area are still loading; try again in a minute."
        )
    et0 = {int(m["month"]): float(m["et0_mm"]) for m in climate["months"]}
    spring = tuple(int(part) for part in climate["frost"]["last_spring"].split("-"))
    autumn = tuple(int(part) for part in climate["frost"]["first_autumn"].split("-"))
    return et0, svc.agronomy.Frost(spring, autumn)


def _gridded_list(calendar: svc.agronomy.Calendar, pool: list[str]) -> str:
    names = [svc.CROP_BY_ID[c].name for c in pool if calendar.is_gridded(c)]
    return ", ".join(names) if names else "none of the offered crops"


@router.post(
    "/strategies/crops",
    response_model=PlannableCrops,
    response_model_by_alias=True,
    summary="Crops with complete planning data for an area",
)
def plannable_crops(request: AreaRequest) -> PlannableCrops:
    market = _market(request)
    return PlannableCrops(country=market.country, crops=_plannable(market))


@router.post(
    "/strategies",
    response_model=StrategiesResponse,
    response_model_by_alias=True,
    summary="Generate and rank multi-year crop rotation strategies",
)
def strategies(request: StrategiesRequest) -> StrategiesResponse:
    market = _market(request)
    suitability = {**_suitability(request.site_context), **request.crop_suitability}
    et0, frost = _climate(request.site_context)
    calendar = svc.agronomy.Calendar(frost, crop_calendar.dates_at(*request.centre()))
    pool = [c for c in _plannable(market) if c in suitability]

    selected = list(dict.fromkeys(request.crops or request.available_crops or []))
    current_crops = list(dict.fromkeys(request.current_crops))
    missing = [c for c in dict.fromkeys(selected + current_crops) if c not in pool]
    if missing:
        raise HTTPException(
            422,
            f"No complete planning data for {', '.join(missing)} here "
            f"(needs agronomy, GAEZ suitability, {market.country} yield and price history).",
        )

    territory = {
        "previously_planted": request.previously_planted,
        "preferred_crops": list(dict.fromkeys([*request.preferred_crops, *selected])),
        "excluded_crops": request.excluded_crops,
        "irrigation_available": request.irrigation_available,
        "calendar": calendar,
        "crop_suitability": {c: suitability[c] for c in pool},
        "crop_water_demand_mm": {c: svc.crop_et_demand_mm(c, et0, calendar) for c in pool},
        "crop_revenue_eur_ha": {c: econ.crop_revenue_eur_ha(c, market) for c in pool},
        "crop_risk": {
            c: svc.revenue_risk(market.crops[c].price_cv, market.crops[c].yield_cv) for c in pool
        },
        "crop_price_cv": {c: market.crops[c].price_cv for c in pool},
        "strategy_weights": {
            name: weights.model_dump(exclude_none=True)
            for name, weights in (request.strategy_weights or {}).items()
        },
    }
    years = max(request.years, len(selected), min(len(selected) + 1, 5))
    territory["baseline_rotation"] = (
        [request.baseline_rotation[i % len(request.baseline_rotation)] for i in range(years)]
        if request.baseline_rotation
        else _default_baseline(pool, years, calendar)
    )

    try:
        selected_rotations = svc.generate_valid_rotations(
            territory, selected, years=years, required_crop_ids=selected,
        )
        ranked = svc.rank_rotation_strategies(
            selected_rotations,
            territory,
            strategy_types=["revenue_first", "soil_builder", "risk_balanced", "water_efficient"],
        )
        balanced_rotations = svc.generate_valid_rotations(
            territory,
            pool,
            years=max(years, len(selected) + 1),
            required_crop_ids=selected,
            require_additional_crop=True,
        ) or selected_rotations
        ranked += svc.rank_rotation_strategies(
            balanced_rotations, territory, strategy_types=["balanced"],
        )
        current = svc.current_strategy(current_crops, territory)
        if current:
            ranked.insert(0, current)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if not ranked:
        raise HTTPException(
            422,
            "No season-compatible rotations can be built from the selected crops. "
            "Add a crop with a different sowing season.",
        )
    for ranking, strategy in enumerate(ranked, start=1):
        strategy["ranking"] = ranking

    area_ha = request.area_ha()
    return StrategiesResponse(
        rotations_evaluated=len(selected_rotations) + len(balanced_rotations),
        baseline_rotation=territory["baseline_rotation"],
        baseline=svc.evaluate_baseline(territory),
        assumptions=[
            f"Sowing and harvest dates come from the {crop_calendar.SOURCE}, rainfed, where this 0.5° cell has an observed calendar for the crop ({_gridded_list(calendar, pool)}); other crops use the typical calendar in crop_agronomy.csv, shifted to this area's frost dates.",
            "Crop families, rooting depth, nitrogen fixation, humus balance and FAO-56 stage proportions come from crop_agronomy.csv (FAO-56, FAO-33, VDLUFA).",
            "Suitability is FAO GAEZ v4 rainfed (1981–2010) for these fields.",
            "Water demand is FAO-56 crop coefficients over these sowing-to-harvest seasons times this area's ERA5 1995–2024 reference-ET normals.",
            f"Revenue is the national 5-year mean yield times the 5-year mean price ({market.country}, Eurostat or FAOSTAT, in {market.price_year} euros).",
            "Income risk is the year-to-year variation of national yields and prices over the last 10 years.",
            "No cost data exists, so costs and profit are not shown; revenue is gross output per hectare.",
            "Erosion risk is not assessed.",
            *(
                ["Your current crops could not be scheduled as one rotation, so it is not shown."]
                if current_crops and not current
                else []
            ),
        ],
        strategies=[{**s, "economics": _economics(s, area_ha, market)} for s in ranked],
    )
