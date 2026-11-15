from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.routers.crops import Bounds, CamelModel, SelectedField
from app.services import strategies as svc

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
    score_components: dict[str, float]
    scoring_weights: dict[str, float]


class StrategyComparison(BaseModel):
    profit_difference_eur_ha: float
    risk_difference: float
    soil_benefit_difference: float
    water_demand_difference: int


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


class StrategiesResponse(CamelModel):
    data_mode: Literal["illustrative_mock"]
    rotations_evaluated: int
    baseline_rotation: list[str]
    baseline: dict[str, object]
    assumptions: list[str]
    strategies: list[Strategy]


@router.post(
    "/strategies",
    response_model=StrategiesResponse,
    response_model_by_alias=True,
    summary="Generate and rank multi-year crop rotation strategies",
)
def strategies(request: StrategiesRequest) -> StrategiesResponse:
    preferred = list(dict.fromkeys([*request.preferred_crops, *request.crops]))
    territory = {
        "bounds": request.bounds.model_dump(by_alias=True) if request.bounds else None,
        "fields": [field.model_dump(by_alias=True) for field in request.fields or []],
        "previously_planted": request.previously_planted,
        "preferred_crops": preferred,
        "excluded_crops": request.excluded_crops,
        "crop_suitability": request.crop_suitability,
        "irrigation_available": request.irrigation_available,
        "baseline_rotation": request.baseline_rotation or list(
            svc.DEFAULT_BASELINE[:request.years]
        ),
        "strategy_weights": {
            name: weights.model_dump(exclude_none=True)
            for name, weights in (request.strategy_weights or {}).items()
        },
    }

    selected_crop_ids = request.crops or request.available_crops or []
    crop_ids = (
        [crop_id for crop_id in selected_crop_ids if crop_id in set(request.available_crops)]
        if request.crops and request.available_crops
        else selected_crop_ids
    )
    unknown_crops = set(selected_crop_ids) - set(svc.CROP_BY_ID)
    if unknown_crops:
        raise HTTPException(
            422, f"Unknown selected crop id(s): {', '.join(sorted(unknown_crops))}"
        )
    try:
        rotations = svc.generate_valid_rotations(
            territory, crop_ids, years=request.years
        )
        ranked = svc.rank_rotation_strategies(rotations, territory)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    if len(ranked) < 5:
        raise HTTPException(
            422,
            f"Only {len(ranked)} distinct valid rotation(s) remain from the selected "
            "crops. Select more compatible crops or relax constraints to generate five strategies.",
        )
    return StrategiesResponse(
        data_mode="illustrative_mock",
        rotations_evaluated=len(rotations),
        baseline_rotation=territory["baseline_rotation"],
        baseline=svc.evaluate_baseline(territory),
        assumptions=[
            "Crop suitability, revenue, costs, soil benefits, risk, and water demand are illustrative mock values.",
            "Use locally validated crop calendars and rotation-gap rules before making farm decisions.",
            "The area is accepted for API compatibility; mock crop values are not spatially derived.",
        ],
        strategies=ranked,
    )
