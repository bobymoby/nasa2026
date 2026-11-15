"""Crop rotation generation and multi-objective strategy ranking.

Crop profiles are built per request from sourced data:

- calendar, plant family, traits, return gap and humus balance:
  `app/data/crop_agronomy.csv` (FAO-56, FAO-33, VDLUFA), with sowing shifted
  to the area's ERA5 frost dates when known
- water demand: FAO-56 crop coefficients × ERA5 reference evapotranspiration
- suitability: FAO GAEZ v4 for the area (`territory["crop_suitability"]`)
- risk: year-to-year variability of national yields and prices
  (`territory["crop_risk"]`, see `crop_risk`)
- profit: national yields × market prices minus estimated costs
  (`territory["crop_profit_eur_ha"]`, from `strategy_economics`)

A value whose source is unavailable is None and scores neutral; it is never
filled in by hand.

Every strategy keeps the farmer's crops and may add crops from the catalog
that suit its goal (`_extra_crops`); the soil- and risk-minded ones fill idle
windows between harvests with cover crops (`COVER_POLICIES`). Picking a
rotation also penalises similarity to the strategies already chosen.
"""

import math
from dataclasses import dataclass
from functools import cache
from itertools import islice, permutations, product
from typing import Any

from app.services import agronomy, cap_payments, drought
from app.services.strategy_economics import COVER_COSTS_EUR_HA, DEFAULT_COVER_COST_EUR_HA

# strategy crop id -> crop_agronomy.csv id
STRATEGY_CROPS = {
    "wheat": "wheat", "barley": "barley", "maize": "maize", "sunflower": "sunflower",
    "rapeseed": "rapeseed", "chickpea": "chickpea", "soybean": "soybean",
    "field_pea": "dry_pea", "oat": "oat", "flax": "flax", "buckwheat": "buckwheat",
    "rye": "rye", "sorghum": "sorghum", "carrot": "carrot",
}
DEEP_ROOTING_M = 1.2
# revenue variability (coefficient of variation) that counts as the highest risk
RISK_CV_CEILING = 0.5
NEUTRAL_SCORE = 50.0


@dataclass(frozen=True)
class CropProfile:
    id: str
    name: str
    family: str
    sow_month: int
    harvest_month: int
    humus_ceq: int
    traits: tuple[str, ...]
    return_gap_positions: int
    role: str
    water_demand_mm: int | None = None
    suitability: float | None = None
    risk: float | None = None
    family_gap_positions: int = 1

    @property
    def duration_months(self) -> int:
        return (self.harvest_month - self.sow_month) % 12 + 1


def _profile(
    crop_id: str,
    record: agronomy.AgronomyCrop,
    frost: agronomy.Frost | None = None,
    et0_monthly_mm: dict[int, float] | None = None,
    suitability: float | None = None,
    risk: float | None = None,
) -> CropProfile:
    phenology = agronomy.water_crop(record, agronomy.Calendar(frost))
    traits = []
    if record.n_fix_kg_ha > 0:
        traits.append("nitrogen_fixing")
    if record.role == "cover":
        traits.append("cover")
    if record.role == "forage":
        traits.append("forage")
    if record.root_depth_m >= DEEP_ROOTING_M:
        traits.append("deep_rooting")
    if record.drought_tolerance == "high":
        traits.append("drought_tolerant")
    water = (
        round(drought.seasonal_etc_mm(phenology, et0_monthly_mm)) if et0_monthly_mm else None
    )
    return CropProfile(
        id=crop_id,
        name=record.name,
        family="+".join(record.family),
        sow_month=phenology.sow[0],
        harvest_month=phenology.harvest[0],
        humus_ceq=record.humus_ceq,
        traits=tuple(traits),
        return_gap_positions=max(1, record.min_return_years),
        role=record.role,
        water_demand_mm=water,
        suitability=suitability,
        risk=risk,
    )


def _cover_records() -> dict[str, agronomy.AgronomyCrop]:
    return {crop_id: r for crop_id, r in agronomy.crops().items() if r.role == "cover"}


CROP_BY_ID = {
    crop_id: _profile(crop_id, agronomy.crops()[record_id])
    for crop_id, record_id in STRATEGY_CROPS.items()
}
COVER_BY_ID = {crop_id: _profile(crop_id, record) for crop_id, record in _cover_records().items()}

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
    "planned": (
        "Planned rotation",
        "The rotation you plan to grow; it is the baseline the other strategies are compared with.",
    ),
    "profit_first": ("Profit-focused", "Prioritizes estimated rotation profit."),
    "soil_builder": ("Soil-building", "Prioritizes the humus balance: legumes and cover crops."),
    "risk_balanced": ("Risk-balanced", "Favors crops whose national yields and prices vary least."),
    "water_efficient": ("Water-efficient", "Favors lower water demand and profit per water unit."),
    "balanced": ("Balanced", "Balances profit, soil benefit, risk, water efficiency, and feasibility."),
}
EXTRA_CROP_REASONS = {
    "profit_first": "the highest estimated profit among the remaining crops",
    "soil_builder": "the best humus balance among the remaining crops",
    "risk_balanced": "steady national yields and prices and a break from your crops' plant families",
    "water_efficient": "low water demand and good profit per mm of water",
    "balanced": "the best all-round score among the remaining crops",
}
# strategy -> (shortest idle window in months worth covering, how to choose the cover)
COVER_POLICIES = {
    "soil_builder": (2, "soil"),
    "risk_balanced": (5, "cheap"),
    "balanced": (6, "value"),
}
NO_COVER_REASONS = {
    "profit_first": "cover crops would add cost without a harvest",
    "water_efficient": "cover crops would draw soil water before the next crop",
}
DEFAULT_BASELINE = ("wheat", "sunflower", "barley", "chickpea", "rapeseed")
MIN_SUITABILITY = 25.0
MAX_FALLOW_MONTHS = 10
MAX_ROTATIONS = 5000
MAX_ROTATION_YEARS = 5
EXTRA_SHORTLIST = 3
BREAK_CROP_BONUS = 5.0
# share of an extra crop's score that comes from the strategy's leading goal alone
GOAL_FOCUS = 0.7
DIVERSITY_PENALTY = 60.0
# cover crops are usually ended a few weeks before the next sowing
COVER_EARLY_END_MONTHS = 1


def crop_risk(yield_cv: float | None, price_cv: float | None) -> float | None:
    """0-100 from the variability of revenue per ha, None without any history.

    Yield and price spreads are combined as if independent; in practice they
    partly offset, so this leans cautious.
    """
    cvs = [cv for cv in (yield_cv, price_cv) if cv is not None]
    if not cvs:
        return None
    revenue_cv = math.sqrt(sum(cv * cv for cv in cvs))
    return round(min(100.0, 100 * revenue_cv / RISK_CV_CEILING), 1)


def profiles(territory: dict[str, Any]) -> dict[str, CropProfile]:
    """Strategy crops and cover crops with this territory's site data, built once per territory."""
    if "_profiles" not in territory:
        frost = territory.get("frost")
        et0 = territory.get("et0_monthly_mm")
        suitability = territory.get("crop_suitability", {})
        risk = territory.get("crop_risk", {})
        built = {
            crop_id: _profile(
                crop_id, agronomy.crops()[record_id], frost, et0,
                suitability.get(crop_id), risk.get(crop_id),
            )
            for crop_id, record_id in STRATEGY_CROPS.items()
        }
        built.update({
            crop_id: _profile(crop_id, record, frost, et0)
            for crop_id, record in _cover_records().items()
        })
        territory["_profiles"] = built
    return territory["_profiles"]


def _covers(territory: dict[str, Any]) -> list[CropProfile]:
    return [crop for crop in profiles(territory).values() if crop.role == "cover"]


def _crop_profile(value: CropProfile | str | dict[str, Any], territory: dict[str, Any]) -> CropProfile:
    if isinstance(value, CropProfile):
        return value
    crop_id = value if isinstance(value, str) else value.get("id")
    if crop_id not in CROP_BY_ID:
        raise ValueError(f"Unknown crop id: {crop_id}")
    return profiles(territory)[crop_id]


def _is_cover(crop: CropProfile) -> bool:
    return crop.role == "cover"


def _cover_cost(crop_id: str) -> float:
    return sum(COVER_COSTS_EUR_HA.get(crop_id, DEFAULT_COVER_COST_EUR_HA))


def _crop_profit(crop: CropProfile, territory: dict[str, Any]) -> float:
    return territory.get("crop_profit_eur_ha", {}).get(crop.id, 0.0)


def _score_or_neutral(value: float | None) -> float:
    return NEUTRAL_SCORE if value is None else value


@cache
def _humus_range() -> tuple[int, int]:
    values = [crop.humus_ceq for crop in agronomy.crops().values()]
    return min(values), max(values)


def _humus_score(ceq_per_season: float) -> float:
    low, high = _humus_range()
    return max(0.0, min(100.0, 100 * (ceq_per_season - low) / (high - low)))


def _available(crops: list[CropProfile], territory: dict[str, Any]) -> list[CropProfile]:
    """Crops not excluded and rated at least MIN_SUITABILITY by GAEZ for rainfed
    farming; with irrigation, rainfed suitability does not limit the choice."""
    excluded = set(territory.get("excluded_crops", ()))
    irrigation = territory.get("irrigation_available", False)
    available = []
    for crop in dict((crop.id, crop) for crop in crops).values():
        if crop.id in excluded:
            continue
        if not irrigation and crop.suitability is not None and crop.suitability < MIN_SUITABILITY:
            continue
        available.append(crop)
    return available


def _rotation_constraint_penalty(
    sequence: tuple[CropProfile, ...],
    history: list[str],
    territory: dict[str, Any],
) -> float:
    prior_crops = [
        (index, profiles(territory)[crop_id])
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


def _slot(crop: CropProfile, earliest_month_index: int) -> dict[str, Any]:
    """The crop sown in its first sowing month at or after the given month index."""
    sow_index = earliest_month_index
    while sow_index % 12 != crop.sow_month - 1:
        sow_index += 1
    harvest_index = sow_index + crop.duration_months - 1
    return {
        "crop": crop,
        "start": (sow_index // 12 + 1, sow_index % 12 + 1),
        "end": (harvest_index // 12 + 1, harvest_index % 12 + 1),
        "sow_index": sow_index,
        "harvest_index": harvest_index,
    }


def _schedule(
    sequence: tuple[CropProfile, ...], max_fallow: int | None = MAX_FALLOW_MONTHS,
) -> list[dict[str, Any]] | None:
    scheduled: list[dict[str, Any]] = []
    for crop in sequence:
        if not scheduled:
            scheduled.append(_slot(crop, crop.sow_month - 1))
            continue
        previous_harvest = scheduled[-1]["harvest_index"]
        item = _slot(crop, previous_harvest + 1)
        if max_fallow is not None and item["sow_index"] - previous_harvest - 1 > max_fallow:
            return None
        scheduled.append(item)
    return scheduled


def _cover_preference(crop: CropProfile, pick: str) -> float:
    if pick == "soil":
        return crop.humus_ceq
    if pick == "cheap":
        return -_cover_cost(crop.id)
    return crop.humus_ceq / _cover_cost(crop.id)


def _add_cover_crops(
    schedule: list[dict[str, Any]],
    strategy_type: str,
    territory: dict[str, Any],
) -> list[dict[str, Any]]:
    """Fill idle windows between harvest and the next sowing per the strategy's cover policy."""
    policy = COVER_POLICIES.get(strategy_type)
    if policy is None:
        return schedule
    min_idle, pick = policy
    covers = _covers(territory)
    out = []
    for item, following in zip(schedule, [*schedule[1:], None]):
        out.append(item)
        if following is None:
            continue
        first_free, last_free = item["harvest_index"] + 1, following["sow_index"] - 1
        if last_free - first_free + 1 < min_idle:
            continue
        fits = [
            slot for cover in covers
            if (slot := _cover_slot(cover, first_free, last_free)) is not None
        ]
        if fits:
            out.append(max(fits, key=lambda slot: _cover_preference(slot["crop"], pick)))
    return out


def _cover_slot(cover: CropProfile, first_free: int, last_free: int) -> dict[str, Any] | None:
    """The cover in the window, ended up to COVER_EARLY_END_MONTHS early so the next crop can be sown."""
    slot = _slot(cover, first_free)
    overrun = slot["harvest_index"] - last_free
    if overrun > COVER_EARLY_END_MONTHS:
        return None
    if overrun > 0:
        slot["harvest_index"] = last_free
        slot["end"] = (last_free // 12 + 1, last_free % 12 + 1)
    return slot


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
    candidates = [_crop_profile(crop, territory) for crop in crops]
    available = _available(candidates, territory)
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
    elif years <= MAX_ROTATION_YEARS:
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
            "rotation_constraint_penalty": _rotation_constraint_penalty(sequence, history, territory),
        })
    return rotations


def _linear_score(value: float, low: float, high: float) -> float:
    if high == low:
        score = 50.0
    else:
        score = 100 * (value - low) / (high - low)
    return max(0.0, min(100.0, score))


def _metrics(rotation: dict[str, Any], territory: dict[str, Any]) -> dict[str, Any]:
    planted: list[CropProfile] = [item["crop"] for item in rotation["schedule"]]
    crops = [crop for crop in planted if not _is_cover(crop)]
    covers = [crop for crop in planted if _is_cover(crop)]
    scores = [_score_or_neutral(crop.suitability) for crop in crops]
    preferred = set(territory.get("preferred_crops", ())) & set(CROP_BY_ID)
    preferred_fraction = (
        len(preferred.intersection(crop.id for crop in crops)) / len(preferred)
        if preferred else 1.0
    )
    years = max(item["end"][0] for item in rotation["schedule"])
    profit = (
        sum(_crop_profit(crop, territory) for crop in crops)
        - sum(_cover_cost(crop.id) - territory.get("cover_payment_eur_ha", 0.0) for crop in covers)
        + years * cap_payments.diversification_eur_ha_year(
            [crop.id for crop in crops], territory.get("area_ha", 0.0)
        )
    )
    # VDLUFA humus equivalents add up; a cover crop counts on top of the main crop
    soil_benefit = _humus_score(sum(crop.humus_ceq for crop in planted) / len(crops))
    risk = sum(_score_or_neutral(crop.risk) for crop in crops) / len(crops)
    water = (
        None if any(crop.water_demand_mm is None for crop in planted)
        else sum(crop.water_demand_mm for crop in planted)
    )
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
    crops = [_crop_profile(crop, territory) for crop in baseline]
    # the baseline is only a reference point, so a long fallow in it is allowed
    schedule = _schedule(tuple(crops), max_fallow=None)
    rotation = {"crop_ids": [crop.id for crop in crops], "schedule": schedule}
    metrics = _metrics(rotation, territory)
    water = metrics["water_demand_mm"]
    metrics["water_efficiency_score"] = (
        round(100 * metrics["profit_eur_ha"] / max(water, 1), 2) if water is not None else None
    )
    return metrics


def planned_strategy(
    crop_ids: list[str], territory: dict[str, Any], is_baseline: bool = False
) -> dict[str, Any] | None:
    """The farmer's planned rotation in the same shape as a ranked strategy.

    The given order is kept when its seasons fit; otherwise the first crop
    order that can be scheduled is used. None if no order fits. When the plan
    is itself the baseline, it is compared with itself.
    """
    if not crop_ids:
        return None
    crops = [_crop_profile(crop_id, territory) for crop_id in crop_ids]
    sequence, schedule = next(
        (
            (order, schedule)
            for order in islice(permutations(crops), MAX_ROTATIONS)
            if (schedule := _schedule(order)) is not None
        ),
        (None, None),
    )
    if sequence is None:
        return None
    rotation = {
        "crop_ids": [crop.id for crop in sequence],
        "schedule": schedule,
        "rotation_constraint_penalty": _rotation_constraint_penalty(
            sequence, list(territory.get("previously_planted", ())), territory
        ),
    }
    metrics = _metrics(rotation, territory)
    water = metrics["water_demand_mm"]
    metrics["water_efficiency_score"] = (
        round(100 * metrics["profit_eur_ha"] / max(water, 1), 2) if water is not None else NEUTRAL_SCORE
    )
    metrics["score_components"] = {}
    metrics["scoring_weights"] = {}
    baseline_ids = territory.get("baseline_rotation") or list(DEFAULT_BASELINE)
    preferred = set(territory.get("preferred_crops", ()))
    strategy = _to_legacy_shape(
        rotation, metrics, "planned", 1, 0.0,
        sorted(preferred.intersection(rotation["crop_ids"])), [],
        metrics if is_baseline else _baseline_metrics(baseline_ids, territory), territory,
    )
    if rotation["crop_ids"] != list(crop_ids):
        strategy["explanations"].insert(
            0, "The crops were reordered so their growing seasons fit one after another."
        )
    return strategy


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


def _difference(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else a - b


def _market_rating(crops: list[CropProfile], territory: dict[str, Any]) -> dict[str, Any]:
    cvs = [
        cv for crop in crops
        if (cv := territory.get("price_cv", {}).get(crop.id)) is not None
    ]
    if not cvs:
        return {"score": 1, "summary": "No market price history for these crops."}
    cv = sum(cvs) / len(cvs)
    return {
        "score": max(1, min(5, 5 - round(cv / 0.1))),
        "summary": (
            f"Prices of {len(cvs)} of {len(crops)} crops vary by {cv:.0%} a year on average "
            "(Eurostat/FAOSTAT, last 10 years); buyer access is not modeled."
        ),
    }


def _pest_rating(crops: list[CropProfile], penalty: float) -> int:
    families = len({crop.family for crop in crops}) / len(crops)
    return _scale_rating(100 * families - 5 * penalty)


def _to_legacy_shape(
    rotation: dict[str, Any],
    metrics: dict[str, Any],
    strategy_type: str,
    ranking: int,
    score: float,
    matched_crops: list[str],
    added_crops: list[str],
    baseline_metrics: dict[str, Any],
    territory: dict[str, Any],
) -> dict[str, Any]:
    planted: list[CropProfile] = [item["crop"] for item in rotation["schedule"]]
    crops = [crop for crop in planted if not _is_cover(crop)]
    soil_score = round(metrics["soil_benefit_score"])
    water = metrics["water_demand_mm"]
    duration_years = max(item["end"][0] for item in rotation["schedule"])
    by_year = [0.0] * duration_years
    if water is not None:
        for item in rotation["schedule"]:
            crop = item["crop"]
            start = (item["start"][0] - 1) * 12 + item["start"][1] - 1
            end = (item["end"][0] - 1) * 12 + item["end"][1] - 1
            monthly = crop.water_demand_mm / (end - start + 1)
            for month_index in range(start, end + 1):
                by_year[month_index // 12] += monthly
        water_use = {
            "level": "low" if water <= 1000 else "medium" if water <= 1400 else "high",
            "summary": "FAO-56 crop coefficients × ERA5 1995–2024 reference evapotranspiration.",
            "by_year_mm": [round(value) for value in by_year],
        }
    else:
        water_use = {
            "level": "unknown",
            "summary": "ERA5 climate data is unavailable, so water demand is not estimated.",
            "by_year_mm": [],
        }
    calendar_note = (
        "Bulgarian calendar from crop_agronomy.csv, sowing shifted to the area's ERA5 frost dates."
        if territory.get("frost") else "Bulgarian calendar from crop_agronomy.csv."
    )

    return {
        "id": f"{strategy_type}-" + "-".join(crop.id for crop in planted),
        "name": STRATEGY_NAMES[strategy_type][0],
        "summary": STRATEGY_NAMES[strategy_type][1],
        "duration_years": duration_years,
        "matched_crops": matched_crops,
        "added_crops": added_crops,
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
                "note": calendar_note,
            }
            for item, crop in zip(rotation["schedule"], planted)
        ],
        "ratings": {
            "nutrient_cycling": {"score": _rating(planted, "nitrogen_fixing"), "summary": "Share of plantings that fix nitrogen (crop_agronomy.csv)."},
            "organic_matter": {"score": _scale_rating(soil_score), "summary": "VDLUFA humus balance of the rotation, cover crops included."},
            "pest_disruption": {"score": _pest_rating(crops, metrics["rotation_constraint_penalty"]), "summary": "Plant-family variety and return gaps between the same crop."},
            "forage_potential": {"score": _rating(planted, "forage"), "summary": "Share of plantings grown for forage."},
            "market": _market_rating(crops, territory),
            "soil_improvement": {"score": _scale_rating(soil_score), "summary": "VDLUFA humus balance of the rotation, cover crops included."},
        },
        "erosion_risk": {
            "level": "low" if soil_score >= 65 else "medium" if soil_score >= 45 else "high",
            "summary": "Proxy from the humus balance; soil loss is not modeled.",
        },
        "water_use": water_use,
        "crop_roles": {
            "nitrogen_fixing": _role_assessment(planted, "nitrogen_fixing"),
            "cover_crops": _role_assessment(planted, "cover"),
            "deep_rooting": _role_assessment(planted, "deep_rooting"),
            "drought_tolerant": _role_assessment(planted, "drought_tolerant"),
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
            "water_demand_difference": _difference(water, baseline_metrics["water_demand_mm"]),
        },
        "explanations": _explanations(metrics, baseline_metrics, planted),
    }


def _scale_rating(value: float) -> int:
    return max(1, min(5, round(value / 20)))


def _rating(crops: list[CropProfile], trait: str) -> int:
    share = sum(trait in crop.traits for crop in crops) / len(crops)
    return max(1, min(5, round(1 + share * 4)))


def _role_assessment(crops: list[CropProfile], trait: str) -> dict[str, str]:
    share = sum(trait in crop.traits for crop in crops) / len(crops)
    level = "high" if share >= 2 / 3 else "medium" if share > 0 else "low"
    return {"level": level, "summary": f"{sum(trait in crop.traits for crop in crops)} of {len(crops)} plantings have this trait (crop_agronomy.csv)."}


def _explanations(metrics: dict[str, Any], baseline: dict[str, Any], crops: list[CropProfile]) -> list[str]:
    differences = (
        ("profit", metrics["profit_eur_ha"] - baseline["profit_eur_ha"], "more", "less"),
        ("soil benefit", metrics["soil_benefit_score"] - baseline["soil_benefit_score"], "higher", "lower"),
        ("risk", metrics["risk_score"] - baseline["risk_score"], "higher", "lower"),
        ("water demand", _difference(metrics["water_demand_mm"], baseline["water_demand_mm"]), "more", "less"),
    )
    explanations = []
    for label, delta, positive, negative in differences:
        if delta is None:
            continue
        if abs(delta) < 0.01:
            explanations.append(f"Modeled {label} is similar to the baseline.")
        else:
            explanations.append(f"Modeled {label} is {positive if delta > 0 else negative} than the baseline.")
    traits = sorted({trait for crop in crops for trait in crop.traits})
    if traits:
        explanations.append("Rotation traits: " + ", ".join(traits) + ".")
    explanations.append("Costs are typical estimates; the other figures come from the data sources listed below.")
    return explanations


def _profiles(territory: dict[str, Any], strategy_types: list[str] | None) -> dict[str, dict[str, float]]:
    weights_by_type = {
        name: weights
        for name, weights in STRATEGY_PROFILES.items()
        if strategy_types is None or name in strategy_types
    }
    for name, overrides in territory.get("strategy_weights", {}).items():
        if name not in STRATEGY_PROFILES:
            raise ValueError(f"Unknown strategy weight profile: {name}")
        if any(value < 0 for value in overrides.values()):
            raise ValueError("strategy weights must be non-negative")
        if name in weights_by_type:
            weights_by_type[name] = {
                component: overrides.get(component, 0.0)
                for component in COMPONENTS
            }
    for name, weights in weights_by_type.items():
        if sum(weights.values()) <= 0:
            raise ValueError(f"strategy weights for {name} must sum to more than zero")
    return weights_by_type


def _weighted(components: dict[str, float], weights: dict[str, float]) -> float:
    total = sum(weights.values())
    return sum(components[key] * weight / total for key, weight in weights.items())


def _extra_crops(
    territory: dict[str, Any],
    selected_ids: list[str],
    weights: dict[str, float],
) -> list[CropProfile]:
    """The crops outside the selection that best serve this strategy's weights."""
    catalog = profiles(territory)
    families = {catalog[crop_id].family for crop_id in selected_ids}
    candidates = [
        crop for crop in _available([catalog[c] for c in CROP_BY_ID], territory)
        if crop.id not in selected_ids
    ]
    if not candidates:
        return []
    profits = [_crop_profit(crop, territory) for crop in candidates]
    demands = [crop.water_demand_mm for crop in candidates]
    known_water = all(d is not None for d in demands)
    efficiencies = [p / max(d, 1) for p, d in zip(profits, demands)] if known_water else []
    profit_range = (min(profits), max(profits))
    top = max(weights.values())
    leading = [key for key, value in weights.items() if value == top]
    goal = leading[0] if len(leading) == 1 else None

    def water_score(index: int) -> float:
        if not known_water:
            return NEUTRAL_SCORE
        return (
            _linear_score(efficiencies[index], min(efficiencies), max(efficiencies))
            + 100 - _linear_score(demands[index], min(demands), max(demands))
        ) / 2

    def score(index: int) -> float:
        crop = candidates[index]
        components = {
            "profit": _linear_score(profits[index], *profit_range),
            "soil_benefit": _humus_score(crop.humus_ceq),
            "risk_control": 100 - _score_or_neutral(crop.risk),
            "water_efficiency": water_score(index),
            "feasibility": _score_or_neutral(crop.suitability),
        }
        overall = _weighted(components, weights)
        focused = (
            GOAL_FOCUS * components[goal] + (1 - GOAL_FOCUS) * overall
            if goal else overall
        )
        bonus = BREAK_CROP_BONUS if crop.family not in families else 0.0
        return focused + bonus

    ranked = sorted(range(len(candidates)), key=lambda i: (-score(i), candidates[i].id))
    return [candidates[i] for i in ranked[:EXTRA_SHORTLIST]]


def _score_candidates(
    rotations: list[dict[str, Any]],
    territory: dict[str, Any],
    strategy_type: str,
    weights: dict[str, float],
) -> list[dict[str, Any]]:
    planned = [
        {**rotation, "schedule": _add_cover_crops(rotation["schedule"], strategy_type, territory)}
        for rotation in rotations
    ]
    metric_list = [_metrics(rotation, territory) for rotation in planned]
    profits = [metrics["profit_eur_ha"] for metrics in metric_list]
    profit_range = (min(profits), max(profits))
    known_water = all(metrics["water_demand_mm"] is not None for metrics in metric_list)
    efficiencies = [
        metrics["profit_eur_ha"] / max(metrics["water_demand_mm"], 1) if known_water else 0.0
        for metrics in metric_list
    ]
    efficiency_range = (min(efficiencies), max(efficiencies))
    candidates = []
    for rotation, metrics, efficiency in zip(planned, metric_list, efficiencies):
        metrics["water_efficiency_score"] = (
            round(_linear_score(efficiency, *efficiency_range), 2) if known_water else NEUTRAL_SCORE
        )
        components = {
            "profit": _linear_score(metrics["profit_eur_ha"], *profit_range),
            "soil_benefit": metrics["soil_benefit_score"],
            "risk_control": 100 - metrics["risk_score"],
            "water_efficiency": metrics["water_efficiency_score"],
            "feasibility": metrics["agronomic_feasibility_score"],
        }
        candidates.append({
            "rotation": rotation,
            "metrics": metrics,
            "components": components,
            "score": _weighted(components, weights),
        })
    return candidates


def _similarity(a: list[str], b: list[str]) -> float:
    """0 = no crop in common, 1 = the same crops in the same order."""
    shared = len(set(a) & set(b)) / len(set(a) | set(b))
    same_position = sum(x == y for x, y in zip(a, b)) / max(len(a), len(b))
    return 0.7 * shared + 0.3 * same_position


def _cover_explanation(strategy_type: str, planted: list[CropProfile]) -> str:
    covers = [crop.name for crop in planted if _is_cover(crop)]
    if covers:
        return f"Cover crops fill {len(covers)} idle window(s) between harvests: {', '.join(covers)}."
    if strategy_type in NO_COVER_REASONS:
        return f"No cover crops: {NO_COVER_REASONS[strategy_type]}."
    return "No idle window between harvests is long enough for a cover crop."


def _rank(
    rotations_by_type: dict[str, list[dict[str, Any]]],
    territory: dict[str, Any],
    selected_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    weights_by_type = _profiles(territory, list(rotations_by_type))
    baseline_ids = territory.get("baseline_rotation") or list(DEFAULT_BASELINE)
    baseline_metrics = _baseline_metrics(baseline_ids, territory)
    preferred = set(territory.get("preferred_crops", ()))
    picked: list[list[str]] = []
    strategies = []
    for strategy_type, weights in weights_by_type.items():
        rotations = rotations_by_type[strategy_type]
        if not rotations:
            continue
        candidates = _score_candidates(rotations, territory, strategy_type, weights)

        def adjusted(candidate: dict[str, Any]) -> float:
            ids = candidate["rotation"]["crop_ids"]
            closest = max((_similarity(ids, other) for other in picked), default=0.0)
            return candidate["score"] - DIVERSITY_PENALTY * closest

        best = min(
            candidates,
            key=lambda c: (-adjusted(c), tuple(c["rotation"]["crop_ids"])),
        )
        rotation, metrics = best["rotation"], best["metrics"]
        crop_ids = rotation["crop_ids"]
        reused_rotation = crop_ids in picked
        picked.append(crop_ids)
        added = (
            [c for c in dict.fromkeys(crop_ids) if c not in selected_ids]
            if selected_ids is not None else []
        )
        strategy = _to_legacy_shape(
            rotation, metrics, strategy_type, len(strategies) + 1, best["score"],
            sorted(preferred.intersection(crop_ids)), added, baseline_metrics, territory,
        )
        explanations = [f"Selected using the {strategy_type.replace('_', ' ')} weighted scoring profile."]
        if added:
            names = ", ".join(profiles(territory)[c].name for c in added)
            explanations.append(f"Adds {names} to your crops for {EXTRA_CROP_REASONS[strategy_type]}.")
        explanations.append(_cover_explanation(strategy_type, [item["crop"] for item in rotation["schedule"]]))
        strategy["explanations"][:0] = explanations
        if reused_rotation:
            strategy["explanations"].append(
                "no distinct sequence remained for this strategy type; the highest-scoring "
                "available sequence is reused."
            )
        strategy["evaluation"]["score_components"] = {
            _camel_key(key): value for key, value in best["components"].items()
        }
        total = sum(weights.values())
        strategy["evaluation"]["scoring_weights"] = {
            _camel_key(key): round(value / total, 3) for key, value in weights.items()
        }
        strategies.append(strategy)
    return strategies


def rank_rotation_strategies(
    rotations: list[dict[str, Any]],
    territory: dict[str, Any],
    strategy_types: list[str] | None = None,
) -> list[dict[str, Any]]:
    """The top rotation from one shared pool for each strategy objective, kept apart from each other."""
    if not rotations:
        return []
    types = [name for name in STRATEGY_PROFILES if strategy_types is None or name in strategy_types]
    return _rank({name: rotations for name in types}, territory)


def build_strategies(
    territory: dict[str, Any],
    selected_ids: list[str],
    years: int,
) -> tuple[list[dict[str, Any]], int]:
    """One strategy per objective; each keeps every selected crop and, when the
    rotation has room, adds crops from the catalog that suit its objective.

    Returns the strategies and how many distinct rotations were evaluated.
    """
    by_extras: dict[tuple[str, ...], list[dict[str, Any]]] = {}

    def rotations_with(extras: list[CropProfile]) -> list[dict[str, Any]]:
        key = tuple(crop.id for crop in extras)
        if key not in by_extras:
            rotations = []
            if extras:
                rotations = generate_valid_rotations(
                    territory, [*selected_ids, *extras], years=years,
                    required_crop_ids=selected_ids, require_additional_crop=True,
                )
            by_extras[key] = rotations or generate_valid_rotations(
                territory, selected_ids, years=years, required_crop_ids=selected_ids,
            )
        return by_extras[key]

    room = years > len(selected_ids)
    rotations_by_type = {
        name: rotations_with(_extra_crops(territory, selected_ids, weights) if room else [])
        for name, weights in _profiles(territory, None).items()
    }
    evaluated = len({
        tuple(rotation["crop_ids"])
        for rotations in by_extras.values()
        for rotation in rotations
    })
    return _rank(rotations_by_type, territory, selected_ids), evaluated


def _camel_key(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)
