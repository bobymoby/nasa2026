import logging
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.routers.crops import Bounds, CamelModel, SelectedField
from app.services import strategies as svc
from app.services import strategy_economics as econ
from app.services.crop_soil_scores import hcat3_codes

log = logging.getLogger(__name__)

router = APIRouter(tags=["strategies"])

StrategyType = Literal[
    "profit_first", "soil_builder", "risk_balanced", "water_efficient", "balanced"
]


class StrategyWeights(CamelModel):
    model_config = ConfigDict(extra="forbid")

    profit: float | None = Field(default=None, ge=0)
    soil_benefit: float | None = Field(default=None, ge=0)
    risk_control: float | None = Field(default=None, ge=0)
    water_efficiency: float | None = Field(default=None, ge=0)
    feasibility: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def has_positive_weight(self) -> "StrategyWeights":
        if not any(value and value > 0 for value in self.model_dump().values()):
            raise ValueError("each scoring profile must include a positive weight")
        return self


class StrategiesRequest(CamelModel):
    # ``crops`` is the set the user selected in the existing strategy-page client.
    crops: list[str] = Field(default_factory=list, max_length=50)
    bounds: Bounds | None = None
    fields: list[SelectedField] | None = Field(default=None, min_length=1, max_length=10_000)
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
    strategy_weights: dict[StrategyType, StrategyWeights] | None = None

    @model_validator(mode="after")
    def validate_request(self) -> "StrategiesRequest":
        if (self.bounds is None) == (self.fields is None):
            raise ValueError("send exactly one of bounds or fields")
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

    def area_ha(self) -> float:
        if self.bounds:
            tl, br = self.bounds.top_left, self.bounds.bottom_right
            ring = [(tl.lat, tl.lng), (tl.lat, br.lng), (br.lat, br.lng), (br.lat, tl.lng)]
            return econ.polygon_area_ha(ring)
        return sum(
            econ.polygon_area_ha([(p.lat, p.lng) for p in f.polygon]) for f in self.fields or []
        )


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


class Evaluation(CamelModel):
    profit_eur_ha: float
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
    profit_difference_eur_ha: float
    risk_difference: float
    soil_benefit_difference: float
    water_demand_difference: int


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
    price_year: int | None
    area_ha: float
    per_ha: Money
    profit_per_ha_year: float
    total: Money
    plantings: list[PlantingMoney]
    by_year: list[YearMoney]


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
    data_mode: Literal["illustrative_mock"]
    rotations_evaluated: int
    baseline_rotation: list[str]
    baseline: dict[str, object]
    assumptions: list[str]
    strategies: list[Strategy]


def _money(e: econ.StrategyEconomics, scale: float = 1.0) -> Money:
    return Money(
        revenue=round(e.revenue * scale),
        seed_cost=round(e.seed_cost * scale),
        operating_cost=round(e.operating_cost * scale),
        total_cost=round(e.total_cost * scale),
        profit=round(e.profit * scale),
    )


def _economics(strategy: dict, area_ha: float) -> Economics:
    e = econ.strategy_economics(strategy["plantings"], strategy["duration_years"])
    return Economics(
        price_year=e.price_year,
        area_ha=round(area_ha, 2),
        per_ha=_money(e),
        profit_per_ha_year=round(e.profit / e.years),
        total=_money(e, area_ha),
        plantings=[
            PlantingMoney(
                crop_name=p.crop_name,
                role=p.role,
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


# crops page ids that differ from the mock crop ids
CROP_ALIASES = {"dry_pea": "field_pea", "peas": "field_pea", "oats": "oat", "flax_linseed": "flax"}
# HCAT3 code // 100 of each mock crop, for crop_soil_scores keys like "winter_barley"
MOCK_HCAT = {
    33010101: "wheat", 33010102: "wheat", 33010104: "barley", 33010106: "maize",
    33010105: "oat", 33010604: "rapeseed", 33010605: "sunflower", 33011600: "soybean",
    33010202: "chickpea", 33010206: "field_pea", 33010607: "flax", 33011502: "buckwheat",
}


def _to_mock_ids(crops: list[str]) -> list[str]:
    unknown = [c for c in crops if c not in svc.CROP_BY_ID and c not in CROP_ALIASES]
    try:
        codes = hcat3_codes(unknown) if unknown else {}
    except Exception:
        log.exception("could not look up HCAT codes")
        codes = {}
    out = []
    for c in crops:
        mapped = CROP_ALIASES.get(c) or (MOCK_HCAT.get(codes[c] // 100) if c in codes else None)
        out.append(mapped or c)
    return list(dict.fromkeys(out))


@router.post(
    "/strategies",
    response_model=StrategiesResponse,
    response_model_by_alias=True,
    summary="Generate and rank multi-year crop rotation strategies",
)
def strategies(request: StrategiesRequest) -> StrategiesResponse:
    request.crops = _to_mock_ids(request.crops)
    if request.available_crops:
        request.available_crops = _to_mock_ids(request.available_crops)
    preferred = list(dict.fromkeys([*request.preferred_crops, *request.crops]))
    territory = {
        "bounds": request.bounds.model_dump(by_alias=True) if request.bounds else None,
        "fields": [field.model_dump(by_alias=True) for field in request.fields or []],
        "previously_planted": request.previously_planted,
        "preferred_crops": preferred,
        "excluded_crops": request.excluded_crops,
        "crop_suitability": request.crop_suitability,
        "irrigation_available": request.irrigation_available,
        "baseline_rotation": request.baseline_rotation or list(svc.DEFAULT_BASELINE),
        "strategy_weights": {
            name: weights.model_dump(exclude_none=True)
            for name, weights in (request.strategy_weights or {}).items()
        },
    }

    selected_crop_ids = list(dict.fromkeys(request.crops or request.available_crops or []))
    crop_ids = selected_crop_ids
    unknown_crops = set(selected_crop_ids) - set(svc.CROP_BY_ID)
    if unknown_crops:
        supported_ids = ", ".join(sorted(svc.CROP_BY_ID))
        raise HTTPException(
            422,
            f"Unknown selected crop id(s): {', '.join(sorted(unknown_crops))}. "
            f"Supported mock crop IDs: {supported_ids}.",
        )
    effective_years = max(request.years, len(crop_ids))
    baseline_source = territory["baseline_rotation"]
    territory["baseline_rotation"] = [
        baseline_source[index % len(baseline_source)]
        for index in range(effective_years)
    ]
    try:
        selected_rotations = svc.generate_valid_rotations(
            territory,
            crop_ids,
            years=effective_years,
            required_crop_ids=crop_ids,
        )
        selected_strategies = svc.rank_rotation_strategies(
            selected_rotations,
            territory,
            strategy_types=[
                "profit_first", "soil_builder", "risk_balanced", "water_efficient"
            ],
        )
        balanced_years = max(effective_years, len(crop_ids) + 1)
        balanced_rotations = svc.generate_valid_rotations(
            territory,
            list(svc.CROP_BY_ID),
            years=balanced_years,
            required_crop_ids=crop_ids,
            require_additional_crop=True,
        )
        if not balanced_rotations:
            balanced_rotations = selected_rotations
        balanced_strategy = svc.rank_rotation_strategies(
            balanced_rotations,
            territory,
            strategy_types=["balanced"],
        )
        ranked = [*selected_strategies, *balanced_strategy]
        for ranking, strategy in enumerate(ranked, start=1):
            strategy["ranking"] = ranking
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    area_ha = request.area_ha()
    if not ranked:
        raise HTTPException(
            422,
            "No season-compatible rotations can be generated from the selected crops "
            "and current suitability/irrigation constraints. Select another supported "
            "crop or enable irrigation where appropriate.",
        )
    return StrategiesResponse(
        data_mode="illustrative_mock",
        rotations_evaluated=len(selected_rotations) + len(balanced_rotations),
        baseline_rotation=territory["baseline_rotation"],
        baseline=svc.evaluate_baseline(territory),
        assumptions=[
            "Crop suitability, revenue, costs, soil benefits, risk, and water demand are illustrative mock values.",
            "Use locally validated crop calendars and rotation-gap rules before making farm decisions.",
            "The first four strategies include every selected crop; the balanced strategy may add another crop.",
            "Rotation length expands when needed to include every selected crop.",
            "The area is accepted for API compatibility; mock crop values are not spatially derived.",
        ],
        strategies=[{**s, "economics": _economics(s, area_ha)} for s in ranked],
    )
