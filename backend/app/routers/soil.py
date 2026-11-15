"""Soil endpoints: WRB soil group at a point, SoilGrids properties, Sentinel-2 indicators."""

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel
from shapely.geometry import Polygon

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
from app.services.wrb import TOP, WrbError, group_shares, top_groups

router = APIRouter(prefix="/soil", tags=["soil"])


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
    classes: Annotated[int, Query(ge=1, le=TOP)] = TOP,
) -> dict:
    try:
        groups = top_groups(lat, lng, classes)
    except WrbError as e:
        raise HTTPException(502, str(e)) from e
    return {
        "lat": lat,
        "lng": lng,
        "soil": groups[0][0] if groups else None,
        "probabilities": [{"soil": soil, "probability": p} for soil, p in groups],
    }


@router.get("/legend", summary="Share of each most probable WRB soil group over a rectangle (Bulgaria)")
def soil_legend(rect: RectQuery) -> list[dict]:
    try:
        shares = group_shares(rect.west, rect.south, rect.east, rect.north)
    except WrbError as e:
        raise HTTPException(502, str(e)) from e
    return [{"soil": soil, "share": share} for soil, share in shares]


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
