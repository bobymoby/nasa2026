from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query

from app.geo import RectQuery
from app.services import heat_frost
from app.services.drought import et0_series
from app.services.weather import WeatherError, era5_daily, power_daily

router = APIRouter(prefix="/climate", tags=["climate"])

FromYear = Annotated[int, Query(ge=1940, description="First year (inclusive)")]
ToYear = Annotated[int, Query(ge=1940, description="Last year (inclusive)")]


def check_years(from_year: int, to_year: int) -> list[int]:
    last = date.today().year - 1
    if to_year > last:
        raise HTTPException(422, f"to_year must be ≤ {last} (last complete year)")
    if from_year > to_year:
        raise HTTPException(422, "from_year must be ≤ to_year")
    return list(range(from_year, to_year + 1))


@router.get("/daily", summary="Daily weather for a rectangle (ERA5 or NASA POWER)")
def daily(
    rect: RectQuery,
    from_year: FromYear = 2024,
    to_year: ToYear = 2024,
    source: Literal["era5", "power"] = "era5",
    with_et0: Annotated[bool, Query(description="Add FAO-56 ET₀ (ERA5 only)")] = False,
):
    years = check_years(from_year, to_year)
    start, end = date(years[0], 1, 1), date(years[-1], 12, 31)
    try:
        s = era5_daily(rect, start, end) if source == "era5" else power_daily(rect, start, end)
    except WeatherError as e:
        raise HTTPException(502, str(e))
    if with_et0 and source == "era5":
        s.values["et0"] = et0_series(s)
    return {
        "rect": rect.as_dict(),
        "source": s.source,
        "points": s.points,
        "elevation_m": round(s.elevation),
        "meta": s.meta,
        "days": s.rows(),
    }


@router.get(
    "/heat-frost",
    summary="Test 1: yearly hot and frost day counts, ERA5 vs NASA POWER",
)
def heat_frost_counts(
    rect: RectQuery,
    from_year: FromYear = 1981,
    to_year: ToYear = 2024,
    july_heat_c: float = 35.0,
    wheat_heat_c: float = 32.0,
    frost_c: float = 0.0,
    disagree_days: Annotated[
        int, Query(ge=1, description="Flag years where sources differ by at least this many days")
    ] = 3,
):
    years = check_years(from_year, to_year)
    start, end = date(years[0], 1, 1), date(years[-1], 12, 31)
    indices = heat_frost.default_indices(july_heat_c, wheat_heat_c, frost_c)
    try:
        sources = {"era5": era5_daily(rect, start, end), "power": power_daily(rect, start, end)}
    except WeatherError as e:
        raise HTTPException(502, str(e))

    counts = {name: heat_frost.yearly_counts(s, indices, years) for name, s in sources.items()}

    series, trends, tops = {}, {}, {}
    disagreements = []
    for idx in indices:
        series[idx.key] = {name: counts[name][idx.key] for name in sources}
        trends[idx.key] = {
            name: heat_frost.trend_per_decade(years, counts[name][idx.key]) for name in sources
        }
        tops[idx.key] = {
            name: heat_frost.top_years(years, counts[name][idx.key]) for name in sources
        }
        for y, a, b in zip(years, counts["era5"][idx.key], counts["power"][idx.key]):
            if a is not None and b is not None and abs(a - b) >= disagree_days:
                disagreements.append(
                    {"year": y, "index": idx.key, "era5": a, "power": b, "diff": a - b}
                )

    return {
        "rect": rect.as_dict(),
        "points": {name: s.points for name, s in sources.items()},
        "indices": {idx.key: idx.describe() for idx in indices},
        "years": years,
        "series": series,  # index -> source -> count per year (aligned with `years`)
        "trend_days_per_decade": trends,
        "top_years": tops,
        "disagreements": disagreements,
    }
