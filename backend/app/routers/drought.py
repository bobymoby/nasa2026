from datetime import date, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query

from app.geo import Rect, RectQuery
from app.routers.climate import FromYear, ToYear, check_years
from app.services.drought import CROPS, SPINUP_DAYS, et0_series, simulate_season
from app.services.weather import WeatherError, era5_daily

router = APIRouter(prefix="/drought", tags=["drought"])

CropParam = Literal["wheat", "maize", "both"]
SoilCapacity = Annotated[
    float, Query(gt=10, le=500, description="Total available soil water (TAW), mm")
]


def _weather(rect: Rect, years: list[int]):
    # wheat seasons start in the previous autumn, plus the bucket spin-up
    start = date(years[0] - 1, 10, 15) - timedelta(days=SPINUP_DAYS)
    end = date(years[-1], 12, 31)
    try:
        s = era5_daily(rect, start, end)
    except WeatherError as e:
        raise HTTPException(502, str(e))
    return s, et0_series(s)


def _crops(crop: CropParam):
    return list(CROPS.values()) if crop == "both" else [CROPS[crop]]


@router.get("", summary="Test 2: seasonal water shortfall and relative yield per crop")
def drought(
    rect: RectQuery,
    from_year: FromYear = 1981,
    to_year: ToYear = 2024,
    crop: CropParam = "both",
    soil_capacity_mm: SoilCapacity = 150.0,
):
    years = check_years(from_year, to_year)
    s, et0 = _weather(rect, years)
    out = {}
    for c in _crops(crop):
        seasons = [
            simulate_season(c, y, s.dates, et0, s.values["precip"], soil_capacity_mm)["summary"]
            for y in years
        ]
        yields = [x["relative_yield"] for x in seasons]
        out[c.key] = {
            "label": c.label,
            "mean_relative_yield": round(sum(yields) / len(yields), 3),
            "worst_years": [
                {"season": x["season"], "relative_yield": x["relative_yield"]}
                for x in sorted(seasons, key=lambda x: x["relative_yield"])[:5]
            ],
            "seasons": seasons,
        }
    return {
        "rect": rect.as_dict(),
        "points": s.points,
        "soil_capacity_mm": soil_capacity_mm,
        "note": "Season is labelled by harvest year (wheat sown the previous October).",
        "crops": out,
    }


@router.get("/season", summary="Daily water-balance trace for one crop season")
def season(
    rect: RectQuery,
    year: Annotated[int, Query(ge=1941, description="Harvest year")],
    crop: Literal["wheat", "maize"] = "maize",
    soil_capacity_mm: SoilCapacity = 150.0,
):
    years = check_years(year, year)
    s, et0 = _weather(rect, years)
    result = simulate_season(CROPS[crop], year, s.dates, et0, s.values["precip"], soil_capacity_mm)
    return {"rect": rect.as_dict(), "points": s.points, "crop": crop, **result}
