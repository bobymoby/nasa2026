import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.routers.crops import Bounds, CamelModel, SelectedField
from app.services import cap_payments as cap
from app.services import strategies as svc
from app.services import strategy_economics as econ
from app.geo import Rect
from app.services import gaez
from app.services.country import country_at
from app.services.crop_soil_scores import hcat3_codes
from app.services.prices.pipeline import Market, market_reference
from app.services.strategy_inputs import SiteInputs, cells_at, cells_in_rect, site_inputs

log = logging.getLogger(__name__)

DEFAULT_COUNTRY = "BG"

router = APIRouter(tags=["strategies"])

WeightedStrategyType = Literal[
    "profit_first", "soil_builder", "risk_balanced", "water_efficient", "balanced"
]
StrategyType = Literal[
    "planned", "profit_first", "soil_builder", "risk_balanced", "water_efficient", "balanced"
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

    def site_area(self) -> tuple[set[gaez.Cell], Rect]:
        """GAEZ cells under the selection and its bounding box."""
        if self.bounds:
            tl, br = self.bounds.top_left, self.bounds.bottom_right
            rect = Rect(tl.lat, tl.lng, br.lat, br.lng)
            return cells_in_rect(rect), rect
        rings = [f.polygon for f in self.fields or []]
        points = [p for ring in rings for p in ring]
        centres = [
            (sum(p.lat for p in ring) / len(ring), sum(p.lng for p in ring) / len(ring))
            for ring in rings
        ]
        rect = Rect(
            max(p.lat for p in points), min(p.lng for p in points),
            min(p.lat for p in points), max(p.lng for p in points),
        )
        return cells_at(centres), rect

    def area_ha(self) -> float:
        if self.bounds:
            tl, br = self.bounds.top_left, self.bounds.bottom_right
            ring = [(tl.lat, tl.lng), (tl.lat, br.lng), (br.lat, br.lng), (br.lat, tl.lng)]
            return econ.polygon_area_ha(ring)
        return sum(
            econ.polygon_area_ha([(p.lat, p.lng) for p in f.polygon]) for f in self.fields or []
        )


class StrategiesRequest(AreaRequest):
    # ``crops`` is the set the user selected in the existing strategy-page client.
    crops: list[str] = Field(default_factory=list, max_length=50)
    planned_crops: list[str] = Field(
        default_factory=list,
        max_length=8,
        description=(
            "Planned crops in planting order, repeats allowed; returned first as the planned "
            "rotation and used as the baseline unless baselineRotation is given."
        ),
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
    water_use_mm: int | None
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
    profit_eur_ha: float
    soil_benefit_score: float
    risk_score: float
    water_demand_mm: int | None
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
    water_demand_difference: int | None


class Money(CamelModel):
    revenue: float
    seed_cost: float
    operating_cost: float
    total_cost: float
    profit: float


class PlantingMoney(CamelModel):
    crop_name: str
    role: Literal["cash", "cover", "forage"]
    yield_t_ha: float | None
    price_eur_t: float | None
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
    country: str
    price_year: int | None
    complete: bool
    missing_data: list[str]
    area_ha: float
    per_ha: Money
    profit_per_ha_year: float
    total: Money
    plantings: list[PlantingMoney]
    by_year: list[YearMoney]
    cap_payments: list["CapPayment"]
    cap_per_ha: float = Field(description="EU CAP payments per ha over one pass through the rotation")
    profit_with_cap_per_ha_year: float
    total_profit_with_cap: float


class CapPayment(CamelModel):
    name: str
    eur_per_ha: float = Field(description="Over one pass through the rotation")
    interventions: list[str]
    claim_year: int
    source: str
    assumption: str | None


class Strategy(CamelModel):
    id: str
    name: str
    summary: str
    duration_years: int
    matched_crops: list[str]
    added_crops: list[str] = Field(description="Crops the strategy adds to the selection for its goal")
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
    data_mode: Literal["sourced_with_estimated_costs"]
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


def _market(request: AreaRequest) -> Market:
    country = country_at(*request.centre())
    if country is None:
        log.info("strategies: no country at %s, using %s", request.centre(), DEFAULT_COUNTRY)
        country = DEFAULT_COUNTRY
    try:
        return market_reference(country, [econ.market_id(c) for c in svc.CROP_BY_ID])
    except Exception:
        log.exception("strategies: market data for %s unavailable", country)
        return Market(country, None, {})


def _economics(strategy: dict, area_ha: float, market: Market) -> Economics:
    e = econ.strategy_economics(strategy["plantings"], strategy["duration_years"], market, area_ha)
    return Economics(
        cap_payments=[
            CapPayment(
                name=line.name,
                eur_per_ha=round(line.eur_per_ha),
                interventions=line.interventions,
                claim_year=line.claim_year,
                source=cap.SOURCE,
                assumption=line.assumption or None,
            )
            for line in e.cap
        ],
        cap_per_ha=round(e.cap_total),
        profit_with_cap_per_ha_year=round(e.profit_with_cap / e.years),
        total_profit_with_cap=round(e.profit_with_cap * area_ha),
        country=e.country,
        price_year=e.price_year,
        complete=e.complete,
        missing_data=e.missing_data,
        area_ha=round(area_ha, 2),
        per_ha=_money(e),
        profit_per_ha_year=round(e.profit / e.years),
        total=_money(e, area_ha),
        plantings=[
            PlantingMoney(
                crop_name=p.crop_name,
                role=p.role,
                yield_t_ha=p.yield_t_ha,
                price_eur_t=p.price_eur_t,
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


def _to_mock_ids(crops: list[str], unique: bool = True) -> list[str]:
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
    return list(dict.fromkeys(out)) if unique else out


class PlannableCrops(CamelModel):
    country: str
    crops: list[str] = Field(description="Crops page ids, e.g. dry_pea")


@router.post(
    "/strategies/crops",
    response_model=PlannableCrops,
    response_model_by_alias=True,
    summary="Crops the strategies can plan for an area: market figures and a cost estimate",
)
def plannable_crops(request: AreaRequest) -> PlannableCrops:
    market = _market(request)
    return PlannableCrops(
        country=market.country,
        crops=[econ.market_id(c) for c in svc.CROP_BY_ID if econ.plannable(c, market)],
    )


@router.post(
    "/strategies",
    response_model=StrategiesResponse,
    response_model_by_alias=True,
    summary="Generate and rank multi-year crop rotation strategies",
)
def strategies(request: StrategiesRequest) -> StrategiesResponse:
    request.crops = _to_mock_ids(request.crops)
    request.planned_crops = _to_mock_ids(request.planned_crops, unique=False)
    if request.available_crops:
        request.available_crops = _to_mock_ids(request.available_crops)
    preferred = list(dict.fromkeys([*request.preferred_crops, *request.crops]))
    with ThreadPoolExecutor(max_workers=2) as pool:
        market_job = pool.submit(_market, request)
        site_job = pool.submit(site_inputs, *request.site_area())
        market, site = market_job.result(), site_job.result()
    area_ha = request.area_ha()
    crop_markets = {c: market.crops.get(econ.market_id(c)) for c in svc.CROP_BY_ID}
    territory = {
        "bounds": request.bounds.model_dump(by_alias=True) if request.bounds else None,
        "fields": [field.model_dump(by_alias=True) for field in request.fields or []],
        "previously_planted": request.previously_planted,
        "preferred_crops": preferred,
        "excluded_crops": request.excluded_crops,
        "crop_suitability": {**site.suitability, **request.crop_suitability},
        "frost": site.frost,
        "et0_monthly_mm": site.et0_monthly_mm,
        "crop_risk": {
            c: risk for c, m in crop_markets.items()
            if m and (risk := svc.crop_risk(m.yield_cv, m.price_cv)) is not None
        },
        "price_cv": {c: m.price_cv for c, m in crop_markets.items() if m and m.price_cv is not None},
        "irrigation_available": request.irrigation_available,
        "baseline_rotation": (
            request.baseline_rotation or request.planned_crops or list(svc.DEFAULT_BASELINE)
        ),
        # area-wide payments (BISS, CRISS) are the same for every plan, so only
        # the crop- and practice-specific ones affect the ranking
        "crop_profit_eur_ha": {
            c: econ.crop_profit_eur_ha(c, market) + cap.crop_payment_eur_ha(c, area_ha)
            for c in svc.CROP_BY_ID
        },
        "cover_payment_eur_ha": cap.cover_payment_eur_ha(area_ha),
        "area_ha": area_ha,
        "strategy_weights": {
            name: weights.model_dump(exclude_none=True)
            for name, weights in (request.strategy_weights or {}).items()
        },
    }

    selected_crop_ids = list(dict.fromkeys(request.crops or request.available_crops or []))
    crop_ids = selected_crop_ids
    unknown_crops = set(selected_crop_ids + request.planned_crops) - set(svc.CROP_BY_ID)
    if unknown_crops:
        supported_ids = ", ".join(sorted(svc.CROP_BY_ID))
        raise HTTPException(
            422,
            f"Unknown selected crop id(s): {', '.join(sorted(unknown_crops))}. "
            f"Supported crop IDs: {supported_ids}.",
        )
    # one slot more than the selection, so each strategy can add a crop of its own
    effective_years = (
        min(svc.MAX_ROTATION_YEARS, max(request.years, len(crop_ids) + 1))
        if len(crop_ids) < svc.MAX_ROTATION_YEARS
        else len(crop_ids)
    )
    baseline_source = territory["baseline_rotation"]
    territory["baseline_rotation"] = [
        baseline_source[index % len(baseline_source)]
        for index in range(effective_years)
    ]
    try:
        ranked, rotations_evaluated = svc.build_strategies(territory, crop_ids, effective_years)
        planned = svc.planned_strategy(
            request.planned_crops, territory, is_baseline=not request.baseline_rotation
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if planned:
        ranked.insert(0, planned)
    for ranking, strategy in enumerate(ranked, start=1):
        strategy["ranking"] = ranking

    if not ranked:
        raise HTTPException(
            422,
            "No season-compatible rotations can be generated from the selected crops "
            "and current suitability/irrigation constraints. Select another supported "
            "crop or enable irrigation where appropriate.",
        )
    notes = _data_notes(market, site, territory)
    for strategy in ranked:
        strategy["market_considerations"].extend(notes)
    return StrategiesResponse(
        data_mode="sourced_with_estimated_costs",
        rotations_evaluated=rotations_evaluated,
        baseline_rotation=territory["baseline_rotation"],
        baseline=svc.evaluate_baseline(territory),
        assumptions=[
            "Revenue is the national 5-year mean yield times the 5-year mean market price "
            f"({market.country}, Eurostat or FAOSTAT, in {market.price_year or 'current'} euros); "
            "crops without published figures or a cost estimate are left out of the totals and flagged.",
            "Seed, operating and cover-crop costs are typical estimates, not measured farm budgets.",
            "EU CAP payments are the amounts Bulgaria actually paid per hectare for claim year 2024 "
            f"({cap.SOURCE}); they assume the farmer applies and meets each scheme's conditions, "
            "and the selected area is treated as the whole farm. Coupled support and eco-schemes "
            "count towards the ranking; BISS and CRISS are the same for every plan.",
            "Crop calendars, plant families, traits, return gaps and humus balances come from "
            "crop_agronomy.csv (FAO-56, FAO-33, VDLUFA); its nitrogen-fixation and drought-tolerance "
            "values have no individual citations.",
            "Risk is the year-to-year variability of national yields (around their trend) and real prices; "
            "it does not cover pests, hail or local conditions.",
            "Erosion risk is a proxy from the humus balance; soil loss is not modeled.",
            "Every strategy includes all selected crops and may add crops chosen for its own goal.",
            "Soil-building, risk-balanced and balanced plans fill idle windows between harvests with cover crops.",
            "Rotations run one year longer than the number of selected crops (at most 5 years), "
            "leaving room for a goal-specific crop.",
            "Every strategy is compared with a baseline: "
            + (
                "the rotation you supplied."
                if request.baseline_rotation
                else "your planned rotation, repeated to the same length."
                if request.planned_crops
                else "a typical regional rotation."
            ),
            *site.warnings,
        ],
        strategies=[{**s, "economics": _economics(s, area_ha, market)} for s in ranked],
    )


def _data_notes(market: Market, site: SiteInputs, territory: dict) -> list[str]:
    """Where this response's figures came from, and what was missing."""
    notes = []
    if site.suitability:
        notes.append("Suitability: FAO GAEZ v4, rainfed, 1981–2010 climate, averaged over the area.")
    if site.et0_monthly_mm:
        notes.append("Water demand: FAO-56 crop coefficients × ERA5 1995–2024 reference evapotranspiration.")
    if site.frost:
        notes.append(
            f"Sowing dates follow ERA5 frost dates for the area (last spring frost {site.frost.last_spring[1]:02d}."
            f"{site.frost.last_spring[0]:02d}, first autumn frost {site.frost.first_autumn[1]:02d}."
            f"{site.frost.first_autumn[0]:02d}, 8 years in 10)."
        )
    if territory["crop_risk"]:
        notes.append(
            f"Risk: variability of {market.country} yields and prices, Eurostat/FAOSTAT, last 10 years."
        )
    else:
        notes.append("Data warning: no yield or price history was available, so risk scores are neutral.")
    notes.extend(f"Data warning: {warning}" for warning in site.warnings)
    return notes
