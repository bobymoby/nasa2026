import logging
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.routers.crops import Bounds, CamelModel, SelectedField
from app.services import strategies as svc
from app.services import strategy_economics as econ
from app.services.country import country_at
from app.services.prices.pipeline import Market, market_reference
from app.services.crop_soil_scores import hcat3_codes

log = logging.getLogger(__name__)

DEFAULT_COUNTRY = "BG"

router = APIRouter(tags=["strategies"])

WeightedStrategyType = Literal[
    "profit_first", "soil_builder", "risk_balanced", "water_efficient", "balanced"
]
StrategyType = Literal[
    "current", "profit_first", "soil_builder", "risk_balanced", "water_efficient", "balanced"
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
    current_crops: list[str] = Field(
        default_factory=list,
        max_length=8,
        description="Crops grown now, in planting order; returned first as the current rotation.",
    )
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
    strategy_weights: dict[WeightedStrategyType, StrategyWeights] | None = None
    site_context: dict[str, Any] | None = None
    context_warnings: list[str] = Field(default_factory=list, max_length=20)

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
    data_mode: Literal[
        "illustrative_mock",
        "mixed_service_and_mock",
        "mixed_dataset_and_mock",
    ]
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


def _market(request: StrategiesRequest) -> Market:
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
    e = econ.strategy_economics(strategy["plantings"], strategy["duration_years"], market)
    return Economics(
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
# HCAT3 code // 100 of each supported strategy crop
MOCK_HCAT = {
    33010101: "wheat", 33010102: "wheat", 33010104: "barley", 33010106: "maize",
    33010105: "oat", 33010604: "rapeseed", 33010605: "sunflower", 33011600: "soybean",
    33010202: "chickpea", 33010206: "field_pea", 33010607: "flax", 33011502: "buckwheat",
}
GAEZ_NAME_TO_MOCK = {
    "wheat": "wheat",
    "winter_wheat": "wheat",
    "winter_common_soft_wheat": "wheat",
    "barley": "barley",
    "winter_barley": "barley",
    "maize": "maize",
    "grain_maize": "maize",
    "grain_maize_corn_popcorn": "maize",
    "sunflower": "sunflower",
    "rapeseed": "rapeseed",
    "winter_rapeseed": "rapeseed",
    "winter_rapeseed_rape": "rapeseed",
    "chickpea": "chickpea",
    "chickpeas": "chickpea",
    "soybean": "soybean",
    "soy_soybeans": "soybean",
    "pea": "field_pea",
    "peas": "field_pea",
    "field_pea": "field_pea",
    "oat": "oat",
    "oats": "oat",
    "flax": "flax",
    "flax_linseed": "flax",
    "buckwheat": "buckwheat",
    "rye": "rye",
    "sorghum": "sorghum",
    "carrot": "carrot",
    "carrots": "carrot",
    "carrots_daucus": "carrot",
}
GAEZ_CODE_TO_MOCK = {
    "whe": "wheat",
    "brl": "barley",
    "mze": "maize",
    "sfl": "sunflower",
    "rsd": "rapeseed",
    "chk": "chickpea",
    "soy": "soybean",
    "pea": "field_pea",
    "oat": "oat",
    "flx": "flax",
    "bck": "buckwheat",
    "rye": "rye",
    "srg": "sorghum",
    "car": "carrot",
    "rcg": "reed_canary_grass",
}


def _prepare_context(
    territory: dict[str, Any],
    site_context: dict[str, Any] | None,
    context_warnings: list[str],
) -> tuple[list[str], list[str]]:
    """Apply available site inputs and produce transparent source notes."""
    notes: list[str] = []
    warnings = list(context_warnings)
    if site_context is None:
        warnings.append("Field-specific strategy context was unavailable.")
        site_context = {}

    warnings.extend(str(error) for error in site_context.get("errors", []))
    plots = site_context.get("plots") or []
    suitability_values: dict[str, list[float]] = {}
    for plot in plots:
        for name, data in (plot.get("crops") or {}).items():
            if not isinstance(data, dict):
                continue
            crop_id = (
                GAEZ_CODE_TO_MOCK.get(data.get("code"))
                or GAEZ_NAME_TO_MOCK.get(name)
            )
            if not crop_id:
                continue
            suitability = data.get("suitability")
            if isinstance(suitability, (int, float)):
                suitability_values.setdefault(crop_id, []).append(float(suitability))

    gaez_scores = {
        crop_id: sum(values) / len(values)
        for crop_id, values in suitability_values.items()
    }
    if gaez_scores:
        territory["crop_suitability"] = {
            **gaez_scores,
            **territory.get("crop_suitability", {}),
        }
        notes.append(
            "FAO GAEZ v4 rainfed suitability (1981–2010) was used in agronomic-feasibility scoring."
        )
    else:
        warnings.append("No mapped FAO GAEZ suitability values were available for the selected crops.")

    if any(
        svc.AGRONOMY_CROP_IDS.get(crop_id, crop_id) in svc.agronomy.crops()
        for crop_id in svc.CROP_BY_ID
    ):
        notes.append(
            "Mapped crop calendars, families, traits, and humus balances use crop_agronomy.csv; "
            "the table documents FAO-56, FAO-33, and VDLUFA references."
        )

    territory["_service_backed_inputs_used"] = bool(gaez_scores)

    sites = [
        plot.get("site") for plot in plots
        if isinstance(plot.get("site"), dict)
    ]
    measured_soils = [
        site for site in sites
        if any(site.get(key) is not None for key in ("soc_g_kg", "sand_pct", "clay_pct"))
    ]
    if measured_soils:
        soil_fields = ("soc_g_kg", "sand_pct", "clay_pct", "awc_mm_per_m")
        soil_summary = {
            key: sum(site[key] for site in measured_soils if site.get(key) is not None)
            / len([site for site in measured_soils if site.get(key) is not None])
            for key in soil_fields
            if any(site.get(key) is not None for site in measured_soils)
        }
        rendered = ", ".join(
            f"{key} {value:.1f}" for key, value in soil_summary.items()
        )
        notes.append(
            f"ISRIC SoilGrids field context (sample mean): {rendered}; these are soil-profile estimates, not direct nutrient measurements."
        )
    climate = site_context.get("climate")
    if isinstance(climate, dict) and climate.get("months"):
        months = climate["months"]
        precip_values = [m.get("precip_mm") for m in months if m.get("precip_mm") is not None]
        et0_values = [m.get("et0_mm") for m in months if m.get("et0_mm") is not None]
        et0_by_month = {}
        for item in months:
            if not isinstance(item, dict):
                continue
            try:
                month = int(item["month"])
                et0 = float(item["et0_mm"])
            except (KeyError, TypeError, ValueError):
                continue
            if month in range(1, 13) and et0 >= 0:
                et0_by_month[month] = et0
        frost_values = climate.get("frost")
        if isinstance(frost_values, dict):
            try:
                spring = tuple(int(part) for part in frost_values["last_spring"].split("-"))
                autumn = tuple(int(part) for part in frost_values["first_autumn"].split("-"))
                date(2024, *spring)
                date(2024, *autumn)
                territory["frost"] = svc.agronomy.Frost(spring, autumn)
            except (KeyError, TypeError, ValueError):
                warnings.append("ERA5 frost-date context was malformed and was not used for crop calendars.")
        if set(et0_by_month) == set(range(1, 13)):
            territory["crop_water_demand_mm"] = {
                crop_id: demand
                for crop_id in svc.CROP_BY_ID
                if (demand := svc.crop_et_demand_mm(
                    crop_id, et0_by_month, territory.get("frost")
                )) is not None
            }
            if territory["crop_water_demand_mm"]:
                notes.append(
                    "Seasonal crop water demand uses FAO-56 crop coefficients with ERA5 1995–2024 monthly reference-ET normals."
                )
                territory["_service_backed_inputs_used"] = True
        climate_summary = []
        if len(precip_values) == 12:
            climate_summary.append(f"annual-normal precipitation {sum(precip_values):.0f} mm")
        if len(et0_values) == 12:
            climate_summary.append(f"annual-normal reference ET0 {sum(et0_values):.0f} mm")
        climate_detail = f" ({', '.join(climate_summary)})" if climate_summary else ""
        notes.append(
            f"ERA5 1995–2024 monthly climate normals{climate_detail} are included as context; no unvalidated crop-stress threshold is inferred."
        )
    if any(plot.get("previous_season") for plot in plots):
        notes.append(
            "Sentinel-2 seasonal history is available as context; crop identity is not inferred from the season classification."
        )
    return notes, list(dict.fromkeys(warnings))


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
    request.current_crops = _to_mock_ids(request.current_crops)
    if request.available_crops:
        request.available_crops = _to_mock_ids(request.available_crops)
    preferred = list(dict.fromkeys([*request.preferred_crops, *request.crops]))
    market = _market(request)
    has_market_data = any(
        crop.price_eur_t is not None and crop.yield_t_ha is not None
        for crop in market.crops.values()
    )
    territory = {
        "bounds": request.bounds.model_dump(by_alias=True) if request.bounds else None,
        "fields": [field.model_dump(by_alias=True) for field in request.fields or []],
        "previously_planted": request.previously_planted,
        "preferred_crops": preferred,
        "excluded_crops": request.excluded_crops,
        "crop_suitability": request.crop_suitability,
        "irrigation_available": request.irrigation_available,
        "baseline_rotation": request.baseline_rotation or list(svc.DEFAULT_BASELINE),
        "crop_profit_eur_ha": {c: econ.crop_profit_eur_ha(c, market) for c in svc.CROP_BY_ID},
        "market_prices": {
            {"dry_pea": "pea"}.get(crop_id, crop_id): {
                "reference_eur_t": crop.price_eur_t
            }
            for crop_id, crop in market.crops.items()
            if crop.price_eur_t is not None
        },
        "strategy_weights": {
            name: weights.model_dump(exclude_none=True)
            for name, weights in (request.strategy_weights or {}).items()
        },
    }
    data_notes, data_warnings = _prepare_context(
        territory,
        request.site_context,
        request.context_warnings,
    )
    territory["_service_backed_inputs_used"] = (
        territory.get("_service_backed_inputs_used", False) or has_market_data
    )

    selected_crop_ids = list(dict.fromkeys(request.crops or request.available_crops or []))
    crop_ids = selected_crop_ids
    unknown_crops = set(selected_crop_ids + request.current_crops) - set(svc.CROP_BY_ID)
    if unknown_crops:
        supported_ids = ", ".join(sorted(svc.CROP_BY_ID))
        raise HTTPException(
            422,
            f"Unknown selected crop id(s): {', '.join(sorted(unknown_crops))}. "
            f"Supported mock crop IDs: {supported_ids}.",
        )
    effective_years = max(
        request.years,
        len(crop_ids),
        min(len(crop_ids) + 1, 5),
    )
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
        current = svc.current_strategy(request.current_crops, territory)
        if request.current_crops and current is None:
            data_warnings.append(
                "Your current crops could not be scheduled as one rotation, so it is not shown."
            )
        ranked = [*([current] if current else []), *selected_strategies, *balanced_strategy]
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
    for strategy in ranked:
        strategy["market_considerations"] = [
            note for note in strategy["market_considerations"]
            if not note.startswith("Profit uses illustrative gross revenue")
        ]
        if not has_market_data:
            strategy["market_considerations"].append(
                "Profit uses illustrative crop revenue and costs where country prices or yield evidence are unavailable."
            )
        strategy["market_considerations"].extend(data_notes)
        strategy["market_considerations"].extend(
            f"Data warning: {warning}" for warning in data_warnings
        )

    return StrategiesResponse(
        data_mode=(
            "mixed_service_and_mock"
            if territory.get("_service_backed_inputs_used")
            else "mixed_dataset_and_mock"
            if any(
                svc.AGRONOMY_CROP_IDS.get(crop_id, crop_id)
                in svc.agronomy.crops()
                for crop_id in svc.CROP_BY_ID
            )
            else "illustrative_mock"
        ),
        rotations_evaluated=len(selected_rotations) + len(balanced_rotations),
        baseline_rotation=territory["baseline_rotation"],
        baseline=svc.evaluate_baseline(territory),
        assumptions=[
            "Mapped crop calendars, families, rooting depth, and humus balance use crop_agronomy.csv; nitrogen-fixation and categorical drought-tolerance values there lack individual source citations.",
            "Crop water demand uses FAO-56 crop coefficients and ERA5 monthly normals where available; it is an ETc estimate, not a forecast of water stress.",
            "The normalized crop humus-balance score is not a forecast of field soil change; erosion risk is not assessed.",
            "Operating costs and modeled crop risk remain estimates; no locally validated farm budgets are available.",
            f"Revenue uses national 5-year mean yields and prices ({market.country}, Eurostat/FAOSTAT, in {market.price_year or 'current'} euros); crops without published figures or cost estimates are left out and flagged.",
            "Published national yields and prices are revenue proxies, not farm yield guarantees; seed and operating costs remain estimates.",
            "Validate crop calendars, rotation-gap rules, and humus-balance assumptions against local agronomy before making farm decisions.",
            "The first four strategies include every selected crop; the balanced strategy may add another crop.",
            "Rotation length expands when needed to include every selected crop.",
            "ISRIC SoilGrids and ERA5 context are shown as measurements/normals, without translating them into unvalidated nutrient or crop-stress diagnoses.",
        ],
        strategies=[{**s, "economics": _economics(s, area_ha, market)} for s in ranked],
    )
