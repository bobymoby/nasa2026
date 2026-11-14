"""Which plants suit a given soil and climate, using FAO ECOCROP requirements.

Data: app/data/ecocrop.csv, the FAO ECOCROP database (2,568 species), copied
from https://github.com/OpenCLIM/ecocrop (EcoCrop_DB.csv), re-encoded from
Windows-1252 to UTF-8. Source: FAO, ECOCROP crop constraints database.

Method (EcoCrop-style):

* Every factor gets a score from 0 (unsuitable) to 1 (optimal).
  Numeric ranges (temperature, rainfall, pH) score 1 inside the optimal range,
  fall linearly to 0 at the absolute limits, and are 0 outside them.
  Categorical factors (texture, depth, drainage, salinity, fertility) score 1
  when the soil meets the optimal requirement, MARGINAL (0.5) when it only
  meets the absolute requirement, and 0 otherwise.
* Temperature is checked only during the crop's growing season: the best
  window of GMIN / 30 consecutive months, rounded to the nearest month
  (wrapping over the new year),
  using monthly mean temperature. A window is rejected if any month's mean
  daily minimum is below the crop's killing temperature (KTMP).
  Perennials must also survive the rest of the year: their mean daily minimum
  outside the window must stay above the rest-period killing temperature
  (KTMPR).
* Rainfall is the annual total against the crop's annual rainfall ranges
  (skipped when irrigated=True).
* The overall score is the minimum of the factor scores: the worst factor
  limits the crop. Factors are skipped (None) when either the soil input or
  the crop's requirement is unknown; crops without temperature requirements
  are left out because their climate fit cannot be judged.

Limits: monthly means hide extremes, so killing-frost checks are lenient
(a month with mean minimum -5 °C can still have -20 °C nights). ECOCROP ranges
are coarse and meant for screening, not yield prediction. Unknown factors do
not limit a crop, so a crop with fewer recorded requirements (or a soil with
fewer known features) can score higher than one checked on more factors.
"growing_months" is the best-fitting window of the crop's shortest cycle,
not a sowing calendar (e.g. winter wheat is sown in autumn).
"""

import csv
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "ecocrop.csv"

# Score for soil that meets only the absolute (not the optimal) requirement.
MARGINAL = 0.5

Texture = Literal["light", "medium", "heavy", "organic"]
Drainage = Literal["poor", "well", "excessive"]
Fertility = Literal["low", "moderate", "high"]

TEXTURES = ("light", "medium", "heavy", "organic")
FERTILITY_ORDER = {"low": 0, "moderate": 1, "high": 2}

# ECOCROP depth class -> minimum soil depth (cm) it needs
DEPTH_MIN_CM = {
    "very shallow": 0.0,
    "shallow": 20.0,
    "medium": 50.0,
    "deep": 150.0,
}

# ECOCROP salinity class -> highest salinity (dS/m) the crop tolerates
SALINITY_MAX_DS_M = {
    "none": 4.0,  # one record; treated like "low"
    "low": 4.0,
    "medium": 10.0,
    "high": math.inf,
}


# ------------------------------------------------------------------ inputs


@dataclass(frozen=True)
class SoilFeatures:
    """Any field may be None (unknown); unknown factors are not scored."""

    ph: float | None = None  # pH in water
    texture: Texture | None = None  # see texture_class()
    depth_cm: float | None = None  # rootable soil depth
    drainage: Drainage | None = None
    salinity_ds_m: float | None = None  # electrical conductivity, dS/m
    fertility: Fertility | None = None


@dataclass(frozen=True)
class ClimateFeatures:
    """Twelve calendar-month values, January first (a climatology)."""

    temperature: Sequence[float]  # mean air temperature, °C
    precipitation: Sequence[float]  # total precipitation, mm/month
    min_temperature: Sequence[float] | None = None  # mean daily minimum, °C

    def __post_init__(self):
        for name in ("temperature", "precipitation", "min_temperature"):
            values = getattr(self, name)
            if values is None:
                continue
            if len(values) != 12 or any(v is None for v in values):
                raise ValueError(f"{name} needs 12 monthly values (Jan..Dec)")


def texture_class(sand_pct: float, clay_pct: float) -> Texture:
    """FAO-style texture group from sand and clay percentages (e.g. SoilGrids).

    heavy  (fine):   clay > 35 %
    light  (coarse): sand > 65 % and clay < 18 %
    medium:          everything else
    """
    if clay_pct > 35:
        return "heavy"
    if sand_pct > 65 and clay_pct < 18:
        return "light"
    return "medium"


def climate_from_monthly(months: Iterable[dict]) -> ClimateFeatures:
    """Average /api/climate/monthly rows into a 12-month climatology.

    Each row needs "month" (YYYY-MM), "temperature", "min_temperature" and
    "precipitation". Every calendar month must have at least one value.
    """
    sums: dict[str, list[list[float]]] = {
        key: [[] for _ in range(12)]
        for key in ("temperature", "min_temperature", "precipitation")
    }
    for row in months:
        m = int(row["month"][5:7]) - 1
        for key in sums:
            if row.get(key) is not None:
                sums[key][m].append(row[key])

    def mean12(key: str) -> list[float]:
        out = []
        for m, values in enumerate(sums[key]):
            if not values:
                raise ValueError(f"no {key} data for month {m + 1:02d}")
            out.append(sum(values) / len(values))
        return out

    return ClimateFeatures(
        temperature=mean12("temperature"),
        min_temperature=mean12("min_temperature"),
        precipitation=mean12("precipitation"),
    )


# ------------------------------------------------------------------ data


def _num(value: str) -> float | None:
    value = (value or "").strip()
    if value in ("", "NA"):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _text(value: str) -> str | None:
    value = (value or "").strip()
    return None if value in ("", "NA") else value


@lru_cache
def load_crops() -> tuple[dict, ...]:
    with open(DATA_FILE, encoding="utf-8", newline="") as f:
        return tuple(csv.DictReader(f))


# ------------------------------------------------------------------ scoring


def _range_score(
    x: float | None,
    abs_min: float | None,
    opt_min: float | None,
    opt_max: float | None,
    abs_max: float | None,
) -> float | None:
    """1 inside [opt_min, opt_max], linear to 0 at the absolute limits."""
    if x is None:
        return None
    # Fill a missing half from the other one; give up if both are missing.
    opt_min = opt_min if opt_min is not None else abs_min
    abs_min = abs_min if abs_min is not None else opt_min
    opt_max = opt_max if opt_max is not None else abs_max
    abs_max = abs_max if abs_max is not None else opt_max
    if None in (abs_min, opt_min, opt_max, abs_max):
        return None
    # Guard against inconsistent records.
    opt_min, opt_max = min(opt_min, opt_max), max(opt_min, opt_max)
    abs_min, abs_max = min(abs_min, opt_min), max(abs_max, opt_max)

    if x < abs_min or x > abs_max:
        return 0.0
    if x < opt_min:
        return (x - abs_min) / (opt_min - abs_min)
    if x > opt_max:
        return (abs_max - x) / (abs_max - opt_max)
    return 1.0


def _two_level(meets_optimal: bool | None, meets_absolute: bool | None) -> float | None:
    """Categorical score from optimal / absolute requirements (None = unknown).

    A missing requirement falls back to the other one.
    """
    if meets_optimal is None:
        meets_optimal = meets_absolute
    if meets_absolute is None:
        meets_absolute = meets_optimal
    if meets_optimal is None:
        return None
    if meets_optimal:
        return 1.0
    return MARGINAL if meets_absolute else 0.0


def _texture_ok(soil: Texture, requirement: str | None) -> bool | None:
    if requirement is None:
        return None
    classes = {c.strip() for c in requirement.split(",")}
    return "wide" in classes or soil in classes


def _drainage_ok(soil: Drainage, requirement: str | None) -> bool | None:
    if requirement is None:
        return None
    keyword = {"poor": "poorly", "well": "well", "excessive": "excessive"}[soil]
    return keyword in requirement


def _depth_ok(depth_cm: float, requirement: str | None) -> bool | None:
    if requirement is None:
        return None
    for name in ("very shallow", "shallow", "medium", "deep"):  # longest match first
        if requirement.startswith(name):
            return depth_cm >= DEPTH_MIN_CM[name]
    return None


def _salinity_ok(ec: float, requirement: str | None) -> bool | None:
    if requirement is None:
        return None
    name = requirement.split(" ")[0]
    if name not in SALINITY_MAX_DS_M:
        return None
    return ec <= SALINITY_MAX_DS_M[name]


def _fertility_ok(soil: Fertility, requirement: str | None) -> bool | None:
    if requirement not in FERTILITY_ORDER:
        return None
    return FERTILITY_ORDER[soil] >= FERTILITY_ORDER[requirement]


def _soil_scores(crop: dict, soil: SoilFeatures) -> dict[str, float | None]:
    scores: dict[str, float | None] = {}
    scores["ph"] = _range_score(
        soil.ph,
        _num(crop["PHMIN"]),
        _num(crop["PHOPMN"]),
        _num(crop["PHOPMX"]),
        _num(crop["PHMAX"]),
    )
    checks = (
        ("texture", soil.texture, _texture_ok, "TEXT", "TEXTR"),
        ("depth", soil.depth_cm, _depth_ok, "DEP", "DEPR"),
        ("drainage", soil.drainage, _drainage_ok, "DRA", "DRAR"),
        ("salinity", soil.salinity_ds_m, _salinity_ok, "SAL", "SALR"),
        ("fertility", soil.fertility, _fertility_ok, "FER", "FERR"),
    )
    for name, value, check, opt_col, abs_col in checks:
        if value is None:
            scores[name] = None
            continue
        scores[name] = _two_level(
            check(value, _text(crop[opt_col])),
            check(value, _text(crop[abs_col])),
        )
    return scores


def _season_months(crop: dict) -> int:
    for col in ("GMIN", "GMAX"):
        days = _num(crop[col])
        if days is not None and days > 0:
            # nearest whole month, halves rounded up (Python's round() is half-even)
            return min(12, max(1, int(days / 30 + 0.5)))
    return 12


def _climate_scores(
    crop: dict, climate: ClimateFeatures
) -> tuple[float | None, float | None, list[int]]:
    """(temperature score, winter-survival score, best growing months 1..12)."""
    limits = [_num(crop[c]) for c in ("TMIN", "TOPMN", "TOPMX", "TMAX")]
    if all(v is None for v in limits):
        return None, None, []

    monthly = [_range_score(t, *limits) for t in climate.temperature]
    if monthly[0] is None:  # not enough temperature limits to score
        return None, None, []

    kill = _num(crop["KTMP"])
    kill_rest = _num(crop["KTMPR"])
    life_span = (crop["LISPA"] or "").lower()
    perennial = "perennial" in life_span and "annual" not in life_span
    tmin = climate.min_temperature
    length = _season_months(crop)

    best: tuple[float, float | None, list[int]] = (0.0, None, [])
    best_combined = -1.0
    for start in range(12 if length < 12 else 1):
        window = [(start + k) % 12 for k in range(length)]
        temp = min(monthly[m] for m in window)
        if kill is not None and tmin is not None and any(tmin[m] < kill for m in window):
            temp = 0.0

        survival = None
        rest = [m for m in range(12) if m not in window]
        if perennial and kill_rest is not None and tmin is not None and rest:
            survival = 0.0 if any(tmin[m] < kill_rest for m in rest) else 1.0

        combined = temp if survival is None else min(temp, survival)
        if combined > best_combined:  # strict: ties keep the earliest start
            best_combined = combined
            best = (temp, survival, [m + 1 for m in window])

    return best


def suitable_crops(
    soil: SoilFeatures,
    climate: ClimateFeatures,
    *,
    min_score: float = 0.5,
    irrigated: bool = False,
    categories: Iterable[str] | None = None,
    exclude_categories: Iterable[str] = ("weed",),
    life_span: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Plants whose ECOCROP requirements fit this soil and climate.

    Args:
        soil: soil features; unknown fields are not scored.
        climate: 12-month climatology (see climate_from_monthly()).
        min_score: keep crops whose overall score is at least this (0..1).
        irrigated: ignore rainfall (water comes from irrigation).
        categories: keep crops whose ECOCROP category contains any of these,
            e.g. ["cereals", "pulses", "vegetables", "fruits & nuts"].
        exclude_categories: drop crops whose category contains any of these
            (default: weeds); pass () to keep everything.
        life_span: keep crops whose life span contains this, e.g. "annual".
        limit: return at most this many crops.

    Returns:
        Crops sorted best first, each with its overall score (the worst
        factor), the limiting factors, every factor score (None = not
        scored) and the best growing months (1 = January).
    """
    wanted = [c.lower() for c in categories] if categories else None
    unwanted = [c.lower() for c in exclude_categories]
    annual_rain = sum(climate.precipitation)
    results = []

    for crop in load_crops():
        category = crop["CAT"] or ""
        if wanted and not any(w in category.lower() for w in wanted):
            continue
        if any(u in category.lower() for u in unwanted):
            continue
        if life_span and life_span.lower() not in (crop["LISPA"] or "").lower():
            continue

        temperature, survival, months = _climate_scores(crop, climate)
        if temperature is None:
            continue  # no temperature requirements: cannot judge climate

        factors: dict[str, float | None] = {"temperature": temperature}
        if survival is not None:
            factors["winter_survival"] = survival
        factors["rainfall"] = None if irrigated else _range_score(
            annual_rain,
            _num(crop["RMIN"]),
            _num(crop["ROPMN"]),
            _num(crop["ROPMX"]),
            _num(crop["RMAX"]),
        )
        factors.update(_soil_scores(crop, soil))

        known = {k: v for k, v in factors.items() if v is not None}
        score = min(known.values())
        if score < min_score:
            continue

        results.append(
            {
                "scientific_name": crop["ScientificName"].strip(),
                "common_names": [
                    n.strip() for n in (crop["COMNAME"] or "").split(",") if n.strip()
                ][:5],
                "family": _text(crop["FAMNAME"]),
                "category": _text(category),
                "life_span": _text(crop["LISPA"]),
                "score": round(score, 3),
                "limiting": [k for k, v in known.items() if v == score and v < 1],
                "factors": {k: (round(v, 3) if v is not None else None) for k, v in factors.items()},
                "growing_months": months,
            }
        )

    results.sort(key=lambda r: (-r["score"], r["scientific_name"]))
    return results[:limit] if limit is not None else results
