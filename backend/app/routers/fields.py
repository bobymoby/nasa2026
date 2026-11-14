"""Agricultural field parcels from the `bulgaria_data` PostGIS table.

The table holds ~1.1M polygons in EPSG:3035, far too many for one GeoJSON
response, so the map loads them as Mapbox vector tiles and fetches the full
record of a single field on click.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Response
from psycopg.rows import dict_row

from app.db import pool
from app.geo import RectQuery
from app.routers.sentinel import Client
from app.services.field_ndvi import (
    S2_FIRST_YEAR,
    Params,
    analyse,
    fetch_observations,
    shrink,
)
from app.services.sentinel_hub import SentinelHubError

router = APIRouter(prefix="/fields", tags=["fields"])

# tiles carry only what the map styles by; the full record comes from /fields/{id}
# below this zoom a tile covers too many fields to render or query quickly
MIN_TILE_ZOOM = 10
TILE_LAYER = "fields"

TILE_SQL = """
WITH bounds AS (
    SELECT ST_TileEnvelope(%(z)s, %(x)s, %(y)s) AS env3857,
           ST_Transform(ST_TileEnvelope(%(z)s, %(x)s, %(y)s), 3035) AS env3035
),
mvt AS (
    SELECT f.field_id,
           f."EC_hcat_n" AS crop_category,
           ST_AsMVTGeom(ST_Transform(f.geometry, 3857), b.env3857) AS geom
    FROM bulgaria_data f, bounds b
    WHERE f.geometry && b.env3035
)
SELECT ST_AsMVT(mvt, %(layer)s, 4096, 'geom')
FROM mvt
WHERE geom IS NOT NULL
"""

FIELD_SQL = """
SELECT field_id,
       farm_id,
       crop_code,
       crop_name,
       "EC_trans_n" AS crop_name_en,
       "EC_hcat_n" AS crop_category,
       "EC_hcat_c" AS crop_category_code,
       organic,
       field_size,
       ST_Area(geometry) / 10000 AS area_ha,
       ST_AsGeoJSON(ST_Transform(geometry, 4326), 7)::json AS geometry
FROM bulgaria_data
WHERE field_id = %(field_id)s
"""


@router.get(
    "/tiles/{z}/{x}/{y}.pbf",
    summary="Field polygons as a Mapbox vector tile (layer `fields`)",
    response_class=Response,
)
def field_tile(
    z: int = Path(ge=0, le=22),
    x: int = Path(ge=0),
    y: int = Path(ge=0),
) -> Response:
    if x >= 2**z or y >= 2**z:
        raise HTTPException(404, "Tile out of range")
    if z < MIN_TILE_ZOOM:
        return Response(status_code=204)

    with pool.connection() as conn:
        (tile,) = conn.execute(
            TILE_SQL, {"z": z, "x": x, "y": y, "layer": TILE_LAYER}
        ).fetchone()

    if not tile:
        return Response(status_code=204)
    return Response(
        bytes(tile),
        media_type="application/vnd.mapbox-vector-tile",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/{field_id}", summary="One field as a GeoJSON feature (WGS84)")
def field(field_id: str) -> dict:
    # row_factory on the cursor, not the pooled connection, or it leaks into
    # whichever request reuses the connection next
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        row = cur.execute(FIELD_SQL, {"field_id": field_id}).fetchone()

    if row is None:
        raise HTTPException(404, f"Field {field_id} not found")
    geometry = row.pop("geometry")
    return {
        "type": "Feature",
        "id": row["field_id"],
        "geometry": geometry,
        "properties": row,
    }


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
        float,
        Query(ge=0, le=200, description="Shrink the field inwards by this many metres"),
    ] = 15,
    min_valid: Annotated[
        float,
        Query(
            gt=0, le=1, description="Drop dates with fewer clear field pixels than this"
        ),
    ] = 0.7,
    bare_ndvi: Annotated[
        float, Query(description="Smoothed NDVI below this = bare")
    ] = 0.20,
    crop_ndvi: Annotated[
        float, Query(description="Smoothed NDVI above this = crop")
    ] = 0.35,
    hold_days: Annotated[
        int, Query(ge=1, le=60, description="Hysteresis hold, days")
    ] = 10,
    gap_days: Annotated[
        int, Query(ge=5, le=120, description="Longer gaps are 'uncertain'")
    ] = 30,
    crop_peak_min: Annotated[
        float, Query(description="Lower peaks after harvest count as green cover")
    ] = 0.5,
    auto_thresholds: Annotated[
        bool,
        Query(
            description="Bare line from this field's baseline, crop line at 50% of its seasonal range"
        ),
    ] = True,
    fill_gaps: Annotated[
        bool, Query(description="Long cloud gaps take the state of the period before")
    ] = True,
    detect_dormancy: Annotated[
        bool,
        Query(
            description="Label the low winter period of autumn-sown crops as dormant_crop"
        ),
    ] = True,
    include_daily: Annotated[
        bool, Query(description="Add the daily cleaned curve")
    ] = False,
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
        min_valid=min_valid,
        bare_ndvi=bare_ndvi,
        crop_ndvi=crop_ndvi,
        hold_days=hold_days,
        gap_days=gap_days,
        crop_peak_min=crop_peak_min,
        auto_thresholds=auto_thresholds,
        fill_gaps=fill_gaps,
        detect_dormancy=detect_dormancy,
    )
    result = analyse(obs, years, params)
    observations, kept, daily = (
        result.pop("observations"),
        result.pop("kept"),
        result.pop("daily"),
    )
    return {
        "rect": rect.as_dict(),
        "analysed_rect": inner.as_dict(),
        "params": params.__dict__,
        **result,
        **(
            {"observations": observations, "kept_observations": kept}
            if include_observations
            else {}
        ),
        **({"daily": daily} if include_daily else {}),
    }
