from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.geo import RectQuery
from app.routers.sentinel import Client
from app.services.field_ndvi import S2_FIRST_YEAR, Params, analyse, fetch_observations, shrink
from app.services.sentinel_hub import SentinelHubError

router = APIRouter(prefix="/fields", tags=["fields"])


@router.get(
    "/ndvi",
    summary="Field greenness: cleaned Sentinel-2 NDVI, crop seasons and cover periods",
)
def field_ndvi(
    sh: Client,
    rect: RectQuery,
    from_year: Annotated[int, Query(ge=S2_FIRST_YEAR)] = 2017,
    to_year: Annotated[int, Query(ge=S2_FIRST_YEAR)] = 2024,
    buffer_m: Annotated[
        float, Query(ge=0, le=200, description="Shrink the field inwards by this many metres")
    ] = 15,
    min_valid: Annotated[
        float, Query(gt=0, le=1, description="Drop dates with fewer clear field pixels than this")
    ] = 0.7,
    bare_ndvi: Annotated[float, Query(description="Smoothed NDVI below this = bare")] = 0.20,
    crop_ndvi: Annotated[float, Query(description="Smoothed NDVI above this = crop")] = 0.35,
    hold_days: Annotated[int, Query(ge=1, le=60, description="Hysteresis hold, days")] = 10,
    gap_days: Annotated[int, Query(ge=5, le=120, description="Longer gaps are 'uncertain'")] = 30,
    crop_peak_min: Annotated[
        float, Query(description="Lower peaks after harvest count as green cover")
    ] = 0.5,
    auto_thresholds: Annotated[
        bool,
        Query(description="Bare line from this field's baseline, crop line at 50% of its seasonal range"),
    ] = True,
    fill_gaps: Annotated[
        bool, Query(description="Long cloud gaps take the state of the period before")
    ] = True,
    detect_dormancy: Annotated[
        bool, Query(description="Label the low winter period of autumn-sown crops as dormant_crop")
    ] = True,
    include_daily: Annotated[bool, Query(description="Add the daily cleaned curve")] = False,
    include_observations: Annotated[
        bool, Query(description="Add every raw acquisition, including dropped ones")
    ] = False,
):
    if from_year > to_year or to_year > date.today().year:
        raise HTTPException(422, "invalid year range")
    if crop_ndvi <= bare_ndvi:
        raise HTTPException(422, "crop_ndvi must be above bare_ndvi")
    try:
        inner = shrink(rect, buffer_m)
    except ValueError as e:
        raise HTTPException(422, str(e))

    years = list(range(from_year, to_year + 1))
    try:
        obs = fetch_observations(sh, inner, years)
    except SentinelHubError as e:
        raise HTTPException(502, str(e))

    params = Params(
        min_valid=min_valid, bare_ndvi=bare_ndvi, crop_ndvi=crop_ndvi,
        hold_days=hold_days, gap_days=gap_days, crop_peak_min=crop_peak_min,
        auto_thresholds=auto_thresholds, fill_gaps=fill_gaps, detect_dormancy=detect_dormancy,
    )
    result = analyse(obs, years, params)
    observations, kept, daily = result.pop("observations"), result.pop("kept"), result.pop("daily")
    return {
        "rect": rect.as_dict(),
        "analysed_rect": inner.as_dict(),
        "params": params.__dict__,
        **result,
        **({"observations": observations, "kept_observations": kept} if include_observations else {}),
        **({"daily": daily} if include_daily else {}),
    }
