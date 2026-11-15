"""Mock-data crop rotation generation and multi-objective strategy ranking.

The crop profiles are illustrative MVP inputs, not locally validated farm
budgets or agronomic recommendations.
"""

from dataclasses import dataclass
from itertools import islice, permutations, product
from typing import Any


@dataclass(frozen=True)
class CropProfile:
    id: str
    name: str
    family: str
    sow_month: int
    harvest_month: int
    gross_revenue_eur_ha: float
    variable_cost_eur_ha: float
    soil_benefit: float
    risk: float
    water_demand_mm: int
    suitability: float
    traits: tuple[str, ...] = ()
    requires_irrigation: bool = False
    return_gap_positions: int = 3
    family_gap_positions: int = 1
    role: str = "cash"
    note: str = ""

    @property
    def duration_months(self) -> int:
        return (self.harvest_month - self.sow_month) % 12 + 1

    @property
    def profit_eur_ha(self) -> float:
        return self.gross_revenue_eur_ha - self.variable_cost_eur_ha


MOCK_CROPS: tuple[CropProfile, ...] = (
    CropProfile("wheat", "Winter wheat", "cereal", 10, 7, 1450, 760, 42, 48, 450, 82,
                ("cover",), note="Illustrative autumn-sown cereal profile."),
    CropProfile("barley", "Winter barley", "cereal", 10, 6, 1250, 680, 45, 42, 380, 76,
                ("cover",), note="Illustrative early-harvest cereal profile."),
    CropProfile("maize", "Maize", "cereal", 4, 9, 1900, 1100, 35, 72, 600, 68,
                requires_irrigation=True, note="Mock profile assumes irrigation is available."),
    CropProfile("sunflower", "Sunflower", "oilseed", 4, 9, 1650, 820, 55, 38, 480, 86,
                ("deep_rooting", "drought_tolerant")),
    CropProfile("rapeseed", "Winter rapeseed", "oilseed", 8, 5, 2050, 1250, 48, 64, 500, 60,
                ("deep_rooting",)),
    CropProfile("chickpea", "Chickpea", "legume", 3, 7, 1750, 900, 78, 40, 300, 58,
                ("nitrogen_fixing", "drought_tolerant")),
    CropProfile("soybean", "Soybean", "legume", 5, 10, 1800, 1050, 72, 55, 500, 52,
                ("nitrogen_fixing",), requires_irrigation=True),
    CropProfile("field_pea", "Field pea", "legume", 3, 7, 1500, 760, 82, 35, 280, 64,
                ("nitrogen_fixing", "cover")),
    CropProfile("oat", "Oat", "cereal", 3, 7, 1320, 700, 48, 40, 350, 70,
                ("cover",), note="Illustrative spring-sown oat profile."),
    CropProfile("flax", "Flax", "oilseed", 3, 8, 1700, 980, 54, 52, 420, 62,
                ("deep_rooting",), note="Illustrative spring-sown flax profile."),
    CropProfile("buckwheat", "Buckwheat", "pseudocereal", 5, 9, 1550, 780, 68, 42, 320, 66,
                ("drought_tolerant",), note="Illustrative warm-season buckwheat profile."),
    CropProfile("rye", "Rye", "cereal", 9, 7, 1280, 670, 58, 38, 330, 68,
                ("cover", "drought_tolerant"), note="Illustrative autumn-sown rye profile."),
    CropProfile("sorghum", "Sorghum", "cereal", 5, 9, 1600, 850, 50, 46, 390, 62,
                ("drought_tolerant",), note="Illustrative warm-season sorghum profile."),
    CropProfile("carrot", "Carrot", "vegetable", 3, 8, 2400, 1500, 52, 50, 430, 60,
                note="Illustrative spring-sown carrot profile."),
    CropProfile(
        "reed_canary_grass", "Reed canary grass", "grass", 4, 8,
        1350, 760, 76, 32, 360, 55,
        ("cover", "deep_rooting"), role="forage",
        note="Illustrative forage profile; perennial stand management is not modeled.",
    ),
)

CROP_BY_ID = {crop.id: crop for crop in MOCK_CROPS}
COMPONENTS = ("profit", "soil_benefit", "risk_control", "water_efficiency", "feasibility")
STRATEGY_PROFILES: dict[str, dict[str, float]] = {
    "profit_first": {
        "profit": 0.45, "soil_benefit": 0.10, "risk_control": 0.10,
        "water_efficiency": 0.10, "feasibility": 0.25,
    },
    "soil_builder": {
        "profit": 0.15, "soil_benefit": 0.40, "risk_control": 0.15,
        "water_efficiency": 0.10, "feasibility": 0.20,
    },
    "risk_balanced": {
        "profit": 0.15, "soil_benefit": 0.15, "risk_control": 0.40,
        "water_efficiency": 0.15, "feasibility": 0.15,
    },
    "water_efficient": {
        "profit": 0.15, "soil_benefit": 0.15, "risk_control": 0.15,
        "water_efficiency": 0.40, "feasibility": 0.15,
    },
    "balanced": {
        "profit": 0.20, "soil_benefit": 0.20, "risk_control": 0.20,
        "water_efficiency": 0.20, "feasibility": 0.20,
    },
}
STRATEGY_NAMES = {
    "profit_first": ("Profit-focused", "Prioritizes estimated rotation profit."),
    "soil_builder": ("Soil-building", "Prioritizes legume, cover, and soil-benefit traits."),
    "risk_balanced": ("Risk-balanced", "Favors more resilient crops with lower modeled risk."),
    "water_efficient": ("Water-efficient", "Favors lower water demand and profit per water unit."),
    "balanced": ("Balanced", "Balances profit, soil benefit, risk, water efficiency, and feasibility."),
}
DEFAULT_BASELINE = ("wheat", "sunflower", "barley", "chickpea", "rapeseed")
MIN_SUITABILITY = 25.0
MAX_FALLOW_MONTHS = 10
MAX_ROTATIONS = 5000


def _crop_profile(value: CropProfile | str | dict[str, Any]) -> CropProfile:
    if isinstance(value, CropProfile):
        return value
    crop_id = value if isinstance(value, str) else value.get("id")
    if crop_id not in CROP_BY_ID:
        raise ValueError(f"Unknown crop id: {crop_id}")
    return CROP_BY_ID[crop_id]


def _available(crops: list[CropProfile], territory: dict[str, Any]) -> list[CropProfile]:
    excluded = set(territory.get("excluded_crops", ()))
    scores = territory.get("crop_suitability", {})
    irrigation = territory.get("irrigation_available", False)
    available = []
    for crop in dict((crop.id, crop) for crop in crops).values():
        suitability = float(scores.get(crop.id, crop.suitability))
        if crop.id in excluded or suitability < MIN_SUITABILITY:
            continue
        if crop.requires_irrigation and not irrigation:
            continue
        available.append(crop)
    return available


def _rotation_constraint_penalty(
    sequence: tuple[CropProfile, ...],
    history: list[str],
) -> float:
    prior_crops = [
        (index, CROP_BY_ID[crop_id])
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
    profiles = [_crop_profile(crop) for crop in crops]
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
            "rotation_constraint_penalty": _rotation_constraint_penalty(sequence, history),
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
    scores = [float(suitability.get(crop.id, crop.suitability)) for crop in crops]
    preferred = set(territory.get("preferred_crops", ())) & set(CROP_BY_ID)
    preferred_fraction = (
        len(preferred.intersection(crop.id for crop in crops)) / len(preferred)
        if preferred else 1.0
    )
    profits = territory.get("crop_profit_eur_ha", {})
    profit = sum(profits.get(crop.id, crop.profit_eur_ha) for crop in crops)
    soil_benefit = sum(crop.soil_benefit for crop in crops) / len(crops)
    risk = sum(crop.risk for crop in crops) / len(crops)
    water = sum(crop.water_demand_mm for crop in crops)
    rotation_penalty = float(rotation.get("rotation_constraint_penalty", 0.0))
    feasibility = max(
        0.0,
        0.8 * (sum(scores) / len(scores)) + 20 * preferred_fraction - rotation_penalty,
    )
    return {
        "profit_eur_ha": round(profit, 2),
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


def _baseline_metrics(baseline: list[str], territory: dict[str, Any]) -> dict[str, Any]:
    crops = [_crop_profile(crop) for crop in baseline]
    schedule = _schedule(tuple(crops))
    if schedule is None:
        raise ValueError("baseline rotation contains incompatible growing seasons")
    rotation = {"crop_ids": [crop.id for crop in crops], "schedule": schedule}
    metrics = _metrics(rotation, territory)
    metrics["water_efficiency_score"] = round(
        100 * metrics["profit_eur_ha"] / max(metrics["water_demand_mm"], 1), 2
    )
    return metrics


def evaluate_baseline(territory: dict[str, Any]) -> dict[str, Any]:
    crop_ids = territory.get("baseline_rotation") or list(DEFAULT_BASELINE)
    metrics = _baseline_metrics(crop_ids, territory)
    return {
        "crop_ids": crop_ids,
        "profit_eur_ha": metrics["profit_eur_ha"],
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
) -> dict[str, Any]:
    crops: list[CropProfile] = [item["crop"] for item in rotation["schedule"]]
    soil_score = round(sum(crop.soil_benefit for crop in crops) / len(crops))
    risk_score = round(sum(crop.risk for crop in crops) / len(crops))
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
        monthly = crop.water_demand_mm / (end - start + 1)
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
                "water_use_mm": crop.water_demand_mm,
                "traits": list(crop.traits),
                "note": crop.note or "Illustrative mock crop profile.",
            }
            for item, crop in zip(rotation["schedule"], crops)
        ],
        "ratings": {
            "nutrient_cycling": {"score": _rating(crops, "nitrogen_fixing"), "summary": "Mock rating based on legume presence."},
            "organic_matter": {"score": _scale_rating(soil_score), "summary": "Mock rating based on residue and cover traits."},
            "pest_disruption": {"score": _scale_rating(100 - risk_score), "summary": "Mock rating based on the crop risk profiles."},
            "forage_potential": {"score": _rating(crops, "forage"), "summary": "Mock rating; none of these crops is modeled as a forage crop."},
            "market": {"score": 3, "summary": "Market access and prices are not modeled."},
            "soil_improvement": {"score": _scale_rating(soil_score), "summary": "Mock rating based on soil-benefit profiles."},
        },
        "erosion_risk": {
            "level": "low" if soil_score >= 65 else "medium" if soil_score >= 45 else "high",
            "summary": "Illustrative risk proxy; not a measured erosion forecast.",
        },
        "water_use": {
            "level": water_level,
            "summary": "Modeled crop water demand from illustrative profiles.",
            "by_year_mm": [round(value) for value in by_year],
        },
        "crop_roles": {
            "nitrogen_fixing": _role_assessment(crops, "nitrogen_fixing"),
            "cover_crops": _role_assessment(crops, "cover"),
            "deep_rooting": _role_assessment(crops, "deep_rooting"),
            "drought_tolerant": _role_assessment(crops, "drought_tolerant"),
        },
        "market_considerations": [
            "Revenue uses national average yields and market prices; seed and other costs are typical estimates.",
            "Check local buyers, your own yields and input costs before use.",
        ],
        "evaluation": metrics,
        "comparison": {
            "profit_difference_eur_ha": round(metrics["profit_eur_ha"] - baseline_metrics["profit_eur_ha"], 2),
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
    return {"level": level, "summary": f"{sum(trait in crop.traits for crop in crops)} of {len(crops)} crops have this modeled trait."}


def _explanations(metrics: dict[str, Any], baseline: dict[str, Any], crops: list[CropProfile]) -> list[str]:
    differences = (
        ("profit", metrics["profit_eur_ha"] - baseline["profit_eur_ha"], "more", "less"),
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
    explanations.append("All values are illustrative mock estimates, not farm-specific predictions.")
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
    profits = [metrics["profit_eur_ha"] for metrics in metric_list]
    water_efficiencies = [
        metrics["profit_eur_ha"] / max(metrics["water_demand_mm"], 1)
        for metrics in metric_list
    ]
    profit_range = (min(profits), max(profits))
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
    strategies = []
    for strategy_type, default_weights in profiles.items():
        weights = default_weights
        weight_total = sum(weights.values())
        if weight_total <= 0:
            raise ValueError(f"strategy weights for {strategy_type} must sum to more than zero")
        scores = []
        for rotation, metrics in zip(rotations, metric_list):
            components = {
                "profit": _linear_score(metrics["profit_eur_ha"], *profit_range),
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
            (index for index in ordered if tuple(rotations[index]["crop_ids"]) not in used_rotations),
            ordered[0],
        )
        rotation_key = tuple(rotations[selected]["crop_ids"])
        reused_rotation = rotation_key in used_rotations
        used_rotations.add(rotation_key)
        metrics = metric_list[selected]
        preferred = set(territory.get("preferred_crops", ()))
        matched = sorted(preferred.intersection(rotations[selected]["crop_ids"]))
        name, summary = STRATEGY_NAMES[strategy_type]
        selected_components = {
            "profit": _linear_score(metrics["profit_eur_ha"], *profit_range),
            "soil_benefit": metrics["soil_benefit_score"],
            "risk_control": 100 - metrics["risk_score"],
            "water_efficiency": metrics["water_efficiency_score"],
            "feasibility": metrics["agronomic_feasibility_score"],
        }
        strategy = _to_legacy_shape(
            rotations[selected], metrics, strategy_type, len(strategies) + 1,
            scores[selected], matched, baseline_metrics,
        )
        strategy["name"] = name
        strategy["summary"] = summary
        strategy["explanations"].insert(
            0, f"Selected using the {strategy_type.replace('_', ' ')} weighted scoring profile."
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
