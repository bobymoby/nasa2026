"""Soil type at a point from ISRIC SoilGrids (WRB reference soil groups, 250 m).

The map draws the soil layer straight from ISRIC's WMS; this endpoint only
answers "what is here" on click. The SoilGrids REST API is slow (~4 s) and
rate-limited (about 5 requests a minute), so answers are cached in Postgres.
"""

from collections.abc import Callable
from typing import Annotated

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel
from shapely.geometry import Polygon

from app.cache import cached_json
from app.geo import Rect, RectQuery
from app.routers.sentinel import Client, DateFrom, DateTo
from app.services.sentinel_hub import SentinelHubError
from app.services.soil import get_soil_characteristics
from app.services.soilgrids import (
    SoilGridsError,
    get_soil_chemistry,
    get_soil_physical,
    get_soil_selection,
)

router = APIRouter(prefix="/soil", tags=["soil"])

SOILGRIDS_CLASSIFICATION_URL = "https://rest.isric.org/soilgrids/v2.0/classification/query"
# SoilGrids cells are 250 m, so ~100 m rounding still hits the same cell and
# lets nearby clicks share a cache entry
COORD_DECIMALS = 3


class SoilSelectionModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class SoilPoint(SoilSelectionModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class SoilBounds(SoilSelectionModel):
    top_left: SoilPoint
    bottom_right: SoilPoint


class SoilField(SoilSelectionModel):
    id: str
    polygon: list[SoilPoint] = Field(min_length=3, max_length=500)


class SoilSelectionRequest(SoilSelectionModel):
    bounds: SoilBounds | None = None
    fields: list[SoilField] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def check_selection(self) -> "SoilSelectionRequest":
        if (self.bounds is None) == (len(self.fields) == 0):
            raise ValueError("provide either bounds or at least one field")
        return self


@router.get("/point", summary="Most probable WRB soil groups at a point (SoilGrids)")
def soil_at_point(
    lat: Annotated[float, Query(ge=-90, le=90)],
    lng: Annotated[float, Query(ge=-180, le=180)],
    classes: Annotated[int, Query(ge=1, le=10)] = 3,
) -> dict:
    lat, lng = round(lat, COORD_DECIMALS), round(lng, COORD_DECIMALS)

    def fetch() -> dict:
        try:
            r = httpx.get(
                SOILGRIDS_CLASSIFICATION_URL,
                params={"lat": lat, "lon": lng, "number_classes": classes},
                timeout=60,
            )
        except httpx.HTTPError as e:
            raise HTTPException(502, f"SoilGrids request failed: {e}")
        if r.status_code == 429:
            raise HTTPException(503, "SoilGrids rate limit reached, try again in a minute")
        if r.status_code != 200:
            raise HTTPException(502, f"SoilGrids returned {r.status_code}")
        data = r.json()
        return {
            "lat": lat,
            "lng": lng,
            # null over sea, lakes and built-up areas
            "soil": data.get("wrb_class_name"),
            "probabilities": [
                {"soil": name, "probability": p}
                for name, p in data.get("wrb_class_probability") or []
                # off-land points list placeholder classes with null probability
                if p is not None
            ],
        }

    return cached_json("soilgrids", {"lat": lat, "lng": lng, "classes": classes}, fetch)


def _soilgrids_response(fetch: Callable[[], dict]) -> dict:
    try:
        return fetch()
    except SoilGridsError as exc:
        status_code = {429: 503, 504: 504}.get(exc.status_code, 502)
        raise HTTPException(status_code, str(exc)) from exc


@router.get(
    "/chemistry",
    summary="Sampled SoilGrids chemistry over a rectangle",
)
def soil_chemistry(rect: RectQuery) -> dict:
    return _soilgrids_response(lambda: get_soil_chemistry(rect))


@router.get(
    "/physical",
    summary="Sampled SoilGrids physical properties over a rectangle",
)
def soil_physical(rect: RectQuery) -> dict:
    return _soilgrids_response(lambda: get_soil_physical(rect))


@router.post(
    "/selection",
    summary="Sample SoilGrids chemistry and physical properties for a selected area",
)
def soil_selection(request: SoilSelectionRequest) -> dict:
    if request.bounds is not None:
        top_left = request.bounds.top_left
        bottom_right = request.bounds.bottom_right
        if (
            top_left.lat <= bottom_right.lat
            or top_left.lng >= bottom_right.lng
        ):
            raise HTTPException(
                422,
                "topLeft must be north-west of bottomRight",
            )
        rect = Rect(
            north=top_left.lat,
            west=top_left.lng,
            south=bottom_right.lat,
            east=bottom_right.lng,
        )
        return _soilgrids_response(lambda: get_soil_selection(rect=rect))

    polygons = []
    for field in request.fields:
        polygon = Polygon([(point.lng, point.lat) for point in field.polygon])
        if not polygon.is_valid or polygon.area == 0:
            raise HTTPException(422, f"Field {field.id} has an invalid polygon")
        polygons.append(polygon)
    return _soilgrids_response(
        lambda: get_soil_selection(polygons=polygons)
    )


@router.get(
    "/soil-characteristics",
    summary="Field-scale Sentinel-2 L2A spectral indicators and historical series",
)
def soil_characteristics(
    sh: Client,
    rect: RectQuery,
    t_from: DateFrom,
    t_to: DateTo,
):
    try:
        return get_soil_characteristics(sh, rect.bbox, t_from, t_to)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    except SentinelHubError as e:
        raise HTTPException(502, str(e)) from e
