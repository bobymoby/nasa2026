"""Agricultural field parcels from the `bulgaria_data` PostGIS table.

The table holds ~1.1M polygons in EPSG:3035, far too many for one GeoJSON
response, so the map loads them as Mapbox vector tiles and fetches the full
record of a single field on click.
"""

from fastapi import APIRouter, HTTPException, Path, Response
from psycopg.rows import dict_row

from app.db import pool

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
    with pool.connection() as conn:
        conn.row_factory = dict_row
        row = conn.execute(FIELD_SQL, {"field_id": field_id}).fetchone()

    if row is None:
        raise HTTPException(404, f"Field {field_id} not found")
    geometry = row.pop("geometry")
    return {"type": "Feature", "id": row["field_id"], "geometry": geometry, "properties": row}
