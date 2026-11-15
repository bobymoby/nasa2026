"""Crop rotation generation and multi-objective strategy ranking.

Every plannable crop is a harvested crop of crop_agronomy.csv. Per request
the router supplies, for every crop it offers, the field's GAEZ suitability,
ERA5-based water demand, market revenue and income risk through the territory
dict (`crop_suitability`, `crop_water_demand_mm`, `crop_revenue_eur_ha`,
`crop_risk`).
"""

import math
from dataclasses import dataclass, replace
from functools import cache
from itertools import islice, permutations, product
from typing import Any

from app.services import agronomy, crop_calendar, drought

DEEP_ROOTING_THRESHOLD_M = 1.2


@dataclass(frozen=True)
class CropProfile:
    id: str
    name: str
    family: str
    sow_month: int
    harvest_month: int
    humus_balance_ceq: int
    traits: tuple[str, ...] = ()
    requires_irrigation: bool = False
    return_gap_positions: int = 3
    family_gap_positions: int = 1
    role: str = "cash"
    gridded_calendar: bool = False

    @property
    def duration_months(self) -> int:
        return (self.harvest_month - self.sow_month) % 12 + 1


def _traits(record: agronomy.AgronomyCrop) -> tuple[str, ...]:
    traits = []
    if record.n_fix_kg_ha > 0:
        traits.append("nitrogen_fixing")
    if record.role == "cover":
        traits.append("cover")
    if record.forage:
        traits.append("forage")
    if record.root_depth_m >= DEEP_ROOTING_THRESHOLD_M:
        traits.append("deep_rooting")
    if record.drought_tolerance == "high":
        traits.append("drought_tolerant")
    return tuple(traits)


def _profile(record: agronomy.AgronomyCrop, calendar: agronomy.Calendar | None = None) -> CropProfile:
    phenology = agronomy.water_crop(record, calendar)
    return CropProfile(
        id=record.crop_id,
        name=record.name,
        family="+".join(record.family),
        sow_month=phenology.sow[0],
        harvest_month=phenology.harvest[0],
        humus_balance_ceq=record.humus_ceq,
        traits=_traits(record),
        return_gap_positions=max(1, record.min_return_years),
        role=record.role,
        gridded_calendar=calendar is not None and calendar.is_gridded(record.crop_id),
    )


CROP_BY_ID: dict[str, CropProfile] = {
    record.crop_id: _profile(record)
    for record in agronomy.crops().values()
    if record.role != "cover"
}


def revenue_risk(price_cv: float, yield_cv: float) -> float:
    """0–100 from the coefficient of variation of revenue per ha; a CV of 0.5 or more is 100."""
    return round(min(100.0, 200 * math.sqrt(price_cv**2 + yield_cv**2)), 1)


COMPONENTS = ("revenue", "soil_benefit", "risk_control", "water_efficiency", "feasibility")
STRATEGY_PROFILES: dict[str, dict[str, float]] = {
    "revenue_first": {
        "revenue": 0.45, "soil_benefit": 0.10, "risk_control": 0.10,
        "water_efficiency": 0.10, "feasibility": 0.25,
    },
    "soil_builder": {
        "revenue": 0.15, "soil_benefit": 0.40, "risk_control": 0.15,
        "water_efficiency": 0.10, "feasibility": 0.20,
    },
    "risk_balanced": {
        "revenue": 0.15, "soil_benefit": 0.15, "risk_control": 0.40,
        "water_efficiency": 0.15, "feasibility": 0.15,
    },
    "water_efficient": {
        "revenue": 0.15, "soil_benefit": 0.15, "risk_control": 0.15,
        "water_efficiency": 0.40, "feasibility": 0.15,
    },
    "balanced": {
        "revenue": 0.20, "soil_benefit": 0.20, "risk_control": 0.20,
        "water_efficiency": 0.20, "feasibility": 0.20,
    },
}
STRATEGY_NAMES = {
    "revenue_first": ("Revenue-focused", "Prioritizes national yield times price per hectare."),
    "soil_builder": ("Soil-building", "Prioritizes legume, cover, and soil-benefit traits."),
    "risk_balanced": ("Risk-balanced", "Favors crops whose national yields and prices vary least."),
    "water_efficient": ("Water-efficient", "Favors lower water demand and revenue per water unit."),
    "balanced": ("Balanced", "Balances revenue, soil benefit, risk, water efficiency, and feasibility."),
}
GRIDDED_NOTE = (
    f"Sowing and harvest from the {crop_calendar.SOURCE} for this 0.5° cell; "
    "family and traits from crop_agronomy.csv."
)
TABLE_NOTE = (
    "No observed gridded calendar for this crop here: sowing and harvest from the typical "
    "calendar in crop_agronomy.csv, shifted to this area's frost dates."
)
DEFAULT_BASELINE = ("wheat", "sunflower", "barley", "chickpea", "rapeseed")
MIN_SUITABILITY = 25.0
MAX_FALLOW_MONTHS = 10
MAX_ROTATIONS = 5000


def _crop_profile(
    value: CropProfile | str | dict[str, Any],
    calendar: agronomy.Calendar | None = None,
) -> CropProfile:
    if isinstance(value, CropProfile):
        profile = value
    else:
        crop_id = value if isinstance(value, str) else value.get("id")
        if crop_id not in CROP_BY_ID:
            raise ValueError(f"Unknown crop id: {crop_id}")
        profile = CROP_BY_ID[crop_id]
    if calendar is None:
        return profile
    shifted = _profile(agronomy.crops()[profile.id], calendar)
    return replace(
        profile,
        sow_month=shifted.sow_month,
        harvest_month=shifted.harvest_month,
        gridded_calendar=shifted.gridded_calendar,
    )


def crop_et_demand_mm(
    crop_id: str,
    et0_monthly_mm: dict[int, float],
    calendar: agronomy.Calendar | None = None,
) -> float | None:
    record = agronomy.crops().get(crop_id)
    if record is None:
        return None
    return drought.reference_crop_et_mm(agronomy.water_crop(record, calendar), et0_monthly_mm)


@cache
def _humus_balance_range() -> tuple[int, int]:
    values = [item.humus_ceq for item in agronomy.crops().values()]
    return min(values), max(values)


def _humus_balance_score(crop: CropProfile) -> float:
    low, high = _humus_balance_range()
    if high == low:
        return 50.0
    return 100 * (crop.humus_balance_ceq - low) / (high - low)


def _available(crops: list[CropProfile], territory: dict[str, Any]) -> list[CropProfile]:
    excluded = set(territory.get("excluded_crops", ()))
    scores = territory.get("crop_suitability", {})
    irrigation = territory.get("irrigation_available", False)
    available = []
    for crop in dict((crop.id, crop) for crop in crops).values():
        suitability = float(scores[crop.id])
        if crop.id in excluded or suitability < MIN_SUITABILITY:
            continue
        if crop.requires_irrigation and not irrigation:
            continue
        available.append(crop)
    return available


def _rotation_constraint_penalty(
    sequence: tuple[CropProfile, ...],
    history: list[str],
    calendar: agronomy.Calendar | None = None,
) -> float:
    prior_crops = [
        (index, _crop_profile(crop_id, calendar))
        for index, crop_id in enumerate(history)
        if crop_id in CROP_BY_ID
    ]
    penalty = 0.0
    for position, candidate in enumerate(sequence):
        current_position = len(history) + position
        same_crop_positions = [
            index for index, crop in prior_crops
            if crop.id == candidate.id
        ]
        family_positions = [
            index for index, crop in prior_crops
            if crop.family == candidate.family
        ]
        for prior_position in same_crop_positions:
            gap = current_position - prior_position
            if gap < candidate.return_gap_positions:
                penalty += 10 * (
                    candidate.return_gap_positions - gap
                ) / candidate.return_gap_positions
        for prior_position in family_positions:
            gap = current_position - prior_position
            if gap <= candidate.family_gap_positions:
                penalty += 5 * (
                    candidate.family_gap_positions + 1 - gap
                ) / (candidate.family_gap_positions + 1)
        prior_crops.append((current_position, candidate))
    return round(penalty, 2)


def _schedule(sequence: tuple[CropProfile, ...]) -> list[dict[str, Any]] | None:
    scheduled: list[dict[str, Any]] = []
    previous_harvest_index: int | None = None
    for crop in sequence:
        if previous_harvest_index is None:
            sow_index = crop.sow_month - 1
        else:
            sow_index = previous_harvest_index + 1
            while sow_index % 12 != crop.sow_month - 1:
                sow_index += 1
            fallow_months = sow_index - previous_harvest_index - 1
            if fallow_months > MAX_FALLOW_MONTHS:
                return None

        harvest_index = sow_index + crop.duration_months - 1
        scheduled.append({
            "crop": crop,
            "start": (sow_index // 12 + 1, sow_index % 12 + 1),
            "end": (harvest_index // 12 + 1, harvest_index % 12 + 1),
            "sow_index": sow_index,
            "harvest_index": harvest_index,
        })
        previous_harvest_index = harvest_index
    return scheduled


def generate_valid_rotations(
    territory: dict[str, Any],
    crops: list[CropProfile | str | dict[str, Any]],
    years: int = 3,
    required_crop_ids: list[str] | None = None,
    require_additional_crop: bool = False,
) -> list[dict[str, Any]]:
    """Enumerate season-compatible crop sequences and score rotation gaps.

    ``previously_planted`` is chronological, oldest to newest. A candidate
    crop sequence has one crop event per requested position; calendar dates
    are scheduled after the prior crop's harvest. When required crops are
    supplied, every returned sequence includes all of them.
    """
    if years < 1:
        raise ValueError("years must be at least 1")
    calendar = territory.get("calendar")
    profiles = [_crop_profile(crop, calendar) for crop in crops]
    available = _available(profiles, territory)
    if not available:
        return []

    required_ids = set(required_crop_ids or ())
    available_by_id = {crop.id: crop for crop in available}
    if not required_ids.issubset(available_by_id):
        return []
    if len(required_ids) > years:
        raise ValueError("years must be at least the number of required crops")

    history = list(territory.get("previously_planted", ()))
    rotations = []
    if required_crop_ids is None:
        sequences = product(available, repeat=years)
    elif years <= 5:
        sequences = (
            sequence for sequence in product(available, repeat=years)
            if required_ids.issubset(crop.id for crop in sequence)
            and (
                not require_additional_crop
                or any(crop.id not in required_ids for crop in sequence)
            )
        )
    elif require_additional_crop and years == len(required_ids) + 1:
        required_crops = [available_by_id[crop_id] for crop_id in required_crop_ids]
        optional_crops = [
            crop for crop in available if crop.id not in required_ids
        ]
        sequences = (
            sequence
            for optional in optional_crops
            for sequence in islice(
                permutations([*required_crops, optional]),
                MAX_ROTATIONS,
            )
        )
    else:
        required_crops = [available_by_id[crop_id] for crop_id in required_crop_ids]
        sequences = islice(permutations(required_crops), MAX_ROTATIONS)

    for sequence in sequences:
        if required_crop_ids is not None and not required_ids.issubset(
            crop.id for crop in sequence
        ):
            continue
        if require_additional_crop and not any(
            crop.id not in required_ids for crop in sequence
        ):
            continue
        sequence = tuple(sequence)
        schedule = _schedule(sequence)
        if schedule is None:
            continue
        rotations.append({
            "crop_ids": [crop.id for crop in sequence],
            "schedule": schedule,
            "rotation_constraint_penalty": _rotation_constraint_penalty(
                sequence, history, calendar
            ),
        })
    return rotations


def _linear_score(value: float, low: float, high: float) -> float:
    if high == low:
        score = 50.0
    else:
        score = 100 * (value - low) / (high - low)
    return max(0.0, min(100.0, score))


def _metrics(rotation: dict[str, Any], territory: dict[str, Any]) -> dict[str, Any]:
    crops: list[CropProfile] = [item["crop"] for item in rotation["schedule"]]
    suitability = territory.get("crop_suitability", {})
    scores = [float(suitability[crop.id]) for crop in crops]
    preferred = set(territory.get("preferred_crops", ())) & set(CROP_BY_ID)
    preferred_fraction = (
        len(preferred.intersection(crop.id for crop in crops)) / len(preferred)
        if preferred else 1.0
    )
    revenues = territory.get("crop_revenue_eur_ha", {})
    revenue = sum(revenues[crop.id] for crop in crops)
    soil_benefit = sum(_humus_balance_score(crop) for crop in crops) / len(crops)
    risks = territory.get("crop_risk", {})
    risk = sum(risks[crop.id] for crop in crops) / len(crops)
    water_demands = territory["crop_water_demand_mm"]
    water = round(sum(water_demands[crop.id] for crop in crops))
    rotation_penalty = float(rotation.get("rotation_constraint_penalty", 0.0))
    feasibility = max(
        0.0,
        0.8 * (sum(scores) / len(scores)) + 20 * preferred_fraction - rotation_penalty,
    )
    return {
        "revenue_eur_ha": round(revenue, 2),
        "soil_benefit_score": round(soil_benefit, 2),
        "risk_score": round(risk, 2),
        "water_demand_mm": water,
        "water_efficiency_score": None,
        "agronomic_feasibility_score": round(feasibility, 2),
        "feasible": True,
        "suitability_scores": [round(score, 2) for score in scores],
        "preferred_crop_coverage": round(preferred_fraction, 3),
        "rotation_constraint_penalty": rotation_penalty,
    }


def schedule_fits(crop_ids: list[str], calendar: agronomy.Calendar | None = None) -> bool:
    return _schedule(tuple(_crop_profile(c, calendar) for c in crop_ids)) is not None


def _baseline_metrics(baseline: list[str], territory: dict[str, Any]) -> dict[str, Any]:
    crops = [_crop_profile(crop, territory.get("calendar")) for crop in baseline]
    schedule = _schedule(tuple(crops))
    if schedule is None:
        raise ValueError("baseline rotation contains incompatible growing seasons")
    rotation = {"crop_ids": [crop.id for crop in crops], "schedule": schedule}
    metrics = _metrics(rotation, territory)
    metrics["water_efficiency_score"] = round(
        100 * metrics["revenue_eur_ha"] / max(metrics["water_demand_mm"], 1), 2
    )
    return metrics


def evaluate_baseline(territory: dict[str, Any]) -> dict[str, Any]:
    crop_ids = territory.get("baseline_rotation") or list(DEFAULT_BASELINE)
    metrics = _baseline_metrics(crop_ids, territory)
    return {
        "crop_ids": crop_ids,
        "revenue_eur_ha": metrics["revenue_eur_ha"],
        "soil_benefit_score": metrics["soil_benefit_score"],
        "risk_score": metrics["risk_score"],
        "water_demand_mm": metrics["water_demand_mm"],
    }


def _to_legacy_shape(
    rotation: dict[str, Any],
    metrics: dict[str, Any],
    strategy_type: str,
    ranking: int,
    score: float,
    matched_crops: list[str],
    baseline_metrics: dict[str, Any],
    territory: dict[str, Any],
) -> dict[str, Any]:
    crops: list[CropProfile] = [item["crop"] for item in rotation["schedule"]]
    soil_score = round(metrics["soil_benefit_score"])
    families = {family for crop in crops for family in crop.family.split("+")}
    water_demands = territory["crop_water_demand_mm"]
    price_cv = sum(territory["crop_price_cv"][crop.id] for crop in crops) / len(crops)
    water_level = (
        "low" if metrics["water_demand_mm"] <= 1000
        else "medium" if metrics["water_demand_mm"] <= 1400
        else "high"
    )
    duration_years = max(item["end"][0] for item in rotation["schedule"])
    by_year = [0.0] * duration_years
    for item in rotation["schedule"]:
        crop = item["crop"]
        start = (item["start"][0] - 1) * 12 + item["start"][1] - 1
        end = (item["end"][0] - 1) * 12 + item["end"][1] - 1
        monthly = water_demands[crop.id] / (end - start + 1)
        for month_index in range(start, end + 1):
            by_year[month_index // 12] += monthly

    return {
        "id": f"{strategy_type}-" + "-".join(rotation["crop_ids"]),
        "name": STRATEGY_NAMES[strategy_type][0],
        "summary": STRATEGY_NAMES[strategy_type][1],
        "duration_years": duration_years,
        "matched_crops": matched_crops,
        "rotation": rotation["crop_ids"],
        "strategy_type": strategy_type,
        "ranking": ranking,
        "score": round(score, 2),
        "plantings": [
            {
                "crop_id": crop.id,
                "crop_name": crop.name,
                "role": crop.role,
                "start": {"year": item["start"][0], "month": item["start"][1]},
                "end": {"year": item["end"][0], "month": item["end"][1]},
                "water_use_mm": round(water_demands[crop.id]),
                "traits": list(crop.traits),
                "note": GRIDDED_NOTE if crop.gridded_calendar else TABLE_NOTE,
            }
            for item, crop in zip(rotation["schedule"], crops)
        ],
        "ratings": {
            "nutrient_cycling": {"score": _rating(crops, "nitrogen_fixing"), "summary": "Share of nitrogen-fixing crops (crop agronomy table)."},
            "organic_matter": {"score": _scale_rating(soil_score), "summary": "Relative VDLUFA humus balance from the crop agronomy table."},
            "pest_disruption": {
                "score": max(1, min(5, round(1 + 4 * (len(families) - 1) / max(len(crops) - 1, 1)))),
                "summary": (
                    f"{len(families)} botanical famil{'y' if len(families) == 1 else 'ies'} in "
                    f"{len(crops)} plantings; changing families breaks pest and disease cycles."
                ),
            },
            "forage_potential": {"score": _rating(crops, "forage"), "summary": "Share of crops usable as forage (crop agronomy table)."},
            "market": {
                "score": _scale_rating(100 - 200 * price_cv),
                "summary": (
                    f"National prices of these crops vary {price_cv:.0%} from year to year "
                    "on average; buyer access is not measured."
                ),
            },
            "soil_improvement": {"score": _scale_rating(soil_score), "summary": "Relative VDLUFA humus balance from the crop agronomy table."},
        },
        "erosion_risk": {
            "level": "unknown",
            "summary": (
                "Not assessed: soil erodibility and slope alone do not quantify "
                "erosion under a proposed crop-management sequence."
            ),
        },
        "water_use": {
            "level": water_level,
            "summary": (
                "Seasonal crop water demand from FAO-56 coefficients and this area's ERA5 normals."
            ),
            "by_year_mm": [round(value) for value in by_year],
        },
        "crop_roles": {
            "nitrogen_fixing": _role_assessment(crops, "nitrogen_fixing"),
            "cover_crops": _role_assessment(crops, "cover"),
            "deep_rooting": _role_assessment(crops, "deep_rooting"),
            "drought_tolerant": _role_assessment(crops, "drought_tolerant"),
        },
        "market_considerations": [
            "Revenue uses national average yields and market prices; costs are not included.",
            "Check local buyers and your own yields before use.",
        ],
        "evaluation": metrics,
        "comparison": {
            "revenue_difference_eur_ha": round(metrics["revenue_eur_ha"] - baseline_metrics["revenue_eur_ha"], 2),
            "risk_difference": round(metrics["risk_score"] - baseline_metrics["risk_score"], 2),
            "soil_benefit_difference": round(metrics["soil_benefit_score"] - baseline_metrics["soil_benefit_score"], 2),
            "water_demand_difference": metrics["water_demand_mm"] - baseline_metrics["water_demand_mm"],
        },
        "explanations": _explanations(metrics, baseline_metrics, crops),
    }


def _scale_rating(value: float) -> int:
    return max(1, min(5, round(value / 20)))


def _rating(crops: list[CropProfile], trait: str) -> int:
    share = sum(trait in crop.traits for crop in crops) / len(crops)
    return max(1, min(5, round(1 + share * 4)))


def _role_assessment(crops: list[CropProfile], trait: str) -> dict[str, str]:
    share = sum(trait in crop.traits for crop in crops) / len(crops)
    level = "high" if share >= 2 / 3 else "medium" if share > 0 else "low"
    return {
        "level": level,
        "summary": (
            f"{sum(trait in crop.traits for crop in crops)} of {len(crops)} crops "
            "have this modeled trait; crop agronomy data are used where available."
        ),
    }


def _explanations(metrics: dict[str, Any], baseline: dict[str, Any], crops: list[CropProfile]) -> list[str]:
    differences = (
        ("revenue", metrics["revenue_eur_ha"] - baseline["revenue_eur_ha"], "more", "less"),
        ("soil benefit", metrics["soil_benefit_score"] - baseline["soil_benefit_score"], "higher", "lower"),
        ("risk", metrics["risk_score"] - baseline["risk_score"], "higher", "lower"),
        ("water demand", metrics["water_demand_mm"] - baseline["water_demand_mm"], "more", "less"),
    )
    explanations = []
    for label, delta, positive, negative in differences:
        if abs(delta) < 0.01:
            explanations.append(f"Modeled {label} is similar to the baseline.")
        elif label == "risk":
            explanations.append(f"Modeled risk is {positive if delta > 0 else negative} than the baseline.")
        else:
            explanations.append(f"Modeled {label} is {positive if delta > 0 else negative} than the baseline.")
    traits = sorted({trait for crop in crops for trait in crop.traits})
    if traits:
        explanations.append("Rotation traits: " + ", ".join(traits) + ".")
    explanations.append("All values are estimates, not farm-specific predictions.")
    return explanations


def rank_rotation_strategies(
    rotations: list[dict[str, Any]],
    territory: dict[str, Any],
    strategy_types: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return the top rotation for each strategy objective, reusing when needed."""
    if not rotations:
        return []
    baseline_ids = territory.get("baseline_rotation") or list(DEFAULT_BASELINE)
    baseline_metrics = _baseline_metrics(baseline_ids, territory)
    metric_list = [_metrics(rotation, territory) for rotation in rotations]
    revenues = [metrics["revenue_eur_ha"] for metrics in metric_list]
    water_efficiencies = [
        metrics["revenue_eur_ha"] / max(metrics["water_demand_mm"], 1)
        for metrics in metric_list
    ]
    revenue_range = (min(revenues), max(revenues))
    water_range = (min(water_efficiencies), max(water_efficiencies))
    for metrics, efficiency in zip(metric_list, water_efficiencies):
        metrics["water_efficiency_score"] = round(
            _linear_score(efficiency, *water_range), 2
        )

    profiles = {
        name: weights
        for name, weights in STRATEGY_PROFILES.items()
        if strategy_types is None or name in strategy_types
    }
    for name, overrides in territory.get("strategy_weights", {}).items():
        if name not in STRATEGY_PROFILES:
            raise ValueError(f"Unknown strategy weight profile: {name}")
        if any(value < 0 for value in overrides.values()):
            raise ValueError("strategy weights must be non-negative")
        if name in profiles:
            profiles[name] = {
                component: overrides.get(component, 0.0)
                for component in COMPONENTS
            }
    used_rotations: set[tuple[str, ...]] = set()
    used_compositions: set[tuple[str, ...]] = set()
    strategies = []
    for strategy_type, default_weights in profiles.items():
        weights = default_weights
        weight_total = sum(weights.values())
        if weight_total <= 0:
            raise ValueError(f"strategy weights for {strategy_type} must sum to more than zero")
        scores = []
        for rotation, metrics in zip(rotations, metric_list):
            components = {
                "revenue": _linear_score(metrics["revenue_eur_ha"], *revenue_range),
                "soil_benefit": metrics["soil_benefit_score"],
                "risk_control": 100 - metrics["risk_score"],
                "water_efficiency": metrics["water_efficiency_score"],
                "feasibility": metrics["agronomic_feasibility_score"],
            }
            normalized = {key: value / weight_total for key, value in weights.items()}
            score = sum(components[key] * weight for key, weight in normalized.items())
            scores.append(score)

        ordered = sorted(
            range(len(rotations)),
            key=lambda index: (
                -scores[index],
                tuple(rotations[index]["crop_ids"]),
            ),
        )
        selected = next(
            (
                index for index in ordered
                if tuple(sorted(rotations[index]["crop_ids"])) not in used_compositions
            ),
            None,
        )
        if selected is None:
            selected = next(
                (
                    index for index in ordered
                    if tuple(rotations[index]["crop_ids"]) not in used_rotations
                ),
                ordered[0],
            )
        rotation_key = tuple(rotations[selected]["crop_ids"])
        composition_key = tuple(sorted(rotation_key))
        reused_rotation = rotation_key in used_rotations
        reused_composition = composition_key in used_compositions
        used_rotations.add(rotation_key)
        used_compositions.add(composition_key)
        metrics = metric_list[selected]
        preferred = set(territory.get("preferred_crops", ()))
        matched = sorted(preferred.intersection(rotations[selected]["crop_ids"]))
        name, summary = STRATEGY_NAMES[strategy_type]
        selected_components = {
            "revenue": _linear_score(metrics["revenue_eur_ha"], *revenue_range),
            "soil_benefit": metrics["soil_benefit_score"],
            "risk_control": 100 - metrics["risk_score"],
            "water_efficiency": metrics["water_efficiency_score"],
            "feasibility": metrics["agronomic_feasibility_score"],
        }
        strategy = _to_legacy_shape(
            rotations[selected], metrics, strategy_type, len(strategies) + 1,
            scores[selected], matched, baseline_metrics, territory,
        )
        strategy["name"] = name
        strategy["summary"] = summary
        strategy["explanations"].insert(
            0, f"Selected using the {strategy_type.replace('_', ' ')} weighted scoring profile."
        )
        if not reused_composition:
            strategy["explanations"].append(
                "A distinct crop mix was chosen to make this strategy's trade-offs clearer."
            )
        if reused_rotation:
            strategy["explanations"].append(
                "no distinct sequence remained for this strategy type; the highest-scoring "
                "available sequence is reused."
            )
        strategy["evaluation"]["water_efficiency_score"] = metrics["water_efficiency_score"]
        strategy["evaluation"]["score_components"] = {
            _camel_key(key): value for key, value in selected_components.items()
        }
        strategy["evaluation"]["scoring_weights"] = {
            _camel_key(key): round(value / weight_total, 3)
            for key, value in weights.items()
        }
        strategies.append(strategy)
    return strategies


def _camel_key(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)
