"""Agricultural field parcels from the `bulgaria_data` PostGIS table.

The table holds ~1.1M polygons in EPSG:3035, far too many for one GeoJSON
response, so the map loads them as Mapbox vector tiles and fetches the full
record of a single field on click.
"""

import contextvars
import hashlib
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Response, status
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel
from shapely.geometry import Polygon

from app.db import pool
from app.geo import Rect, RectQuery
from app.routers.crops import Point as LatLng
from app.routers.crops import SelectedField
from app.routers.sentinel import Client
from app.services.field_ndvi import (
    S2_FIRST_YEAR,
    Params,
    analyse,
    fetch_observations,
    plot_polygon,
    shrink,
    shrink_polygon,
)
from app.services.sentinel_hub import SentinelHub, SentinelHubError
from app.timing import emit, plot_tag, timed

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


# ---------------------------------------------------------------- NDVI periods
# registered before /{field_id}, which would otherwise capture "ndvi"

MAX_PLOTS = 50
DEFAULT_FROM_YEAR = 2023

log = logging.getLogger("app.fields")

# background workers for /ndvi/prefetch; each plot fans out over its years
_prefetch_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ndvi-prefetch")


class NdviOptions(BaseModel):
    from_year: int = Field(DEFAULT_FROM_YEAR, ge=S2_FIRST_YEAR)
    to_year: int = Field(default_factory=lambda: date.today().year, ge=S2_FIRST_YEAR)
    buffer_m: float = Field(15, ge=0, le=200, description="Shrink the plot inwards by this many metres")
    min_valid: float = Field(0.7, gt=0, le=1, description="Drop dates with fewer clear plot pixels than this")
    bare_ndvi: float = Field(0.20, description="Smoothed NDVI below this = barren")
    crop_ndvi: float = Field(0.35, description="Smoothed NDVI above this = crop")
    hold_days: int = Field(10, ge=1, le=60, description="Hysteresis hold, days")
    gap_days: int = Field(30, ge=5, le=120, description="Longer gaps without clear images are gap-filled")
    crop_peak_min: float = Field(0.5, description="Lower peaks after harvest count as green cover")
    auto_thresholds: bool = Field(
        True, description="Bare line from this plot's baseline, crop line at 50% of its seasonal range"
    )
    fill_gaps: bool = Field(True, description="Long cloud gaps take the state of the period before")
    detect_dormancy: bool = Field(
        True, description="Label the low winter period of autumn-sown crops as dormant_crop"
    )
    detail: bool = Field(
        False, description="Return the full analysis (seasons, yearly summary, thresholds) instead"
    )
    include_daily: bool = Field(False, description="With detail: add the daily cleaned curve")
    include_observations: bool = Field(
        False, description="With detail: add every raw acquisition, including dropped ones"
    )

    def params(self) -> Params:
        return Params(
            min_valid=self.min_valid, bare_ndvi=self.bare_ndvi, crop_ndvi=self.crop_ndvi,
            hold_days=self.hold_days, gap_days=self.gap_days, crop_peak_min=self.crop_peak_min,
            auto_thresholds=self.auto_thresholds, fill_gaps=self.fill_gaps,
            detect_dormancy=self.detect_dormancy,
        )

    def years(self) -> list[int]:
        if self.from_year > self.to_year or self.to_year > date.today().year:
            raise HTTPException(422, "invalid year range")
        if self.crop_ndvi <= self.bare_ndvi:
            raise HTTPException(422, "crop_ndvi must be above bare_ndvi")
        return list(range(self.from_year, self.to_year + 1))


class PlotsRequest(NdviOptions):
    """Outlines as plain [lat, lon] lists (`plots`), the frontend's picked
    fields (`fields`, same shape as /api/crops/fields), or both.

    Keys may be snake_case or camelCase (fromYear, bufferM, ...).
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    plots: list[list[tuple[float, float] | LatLng]] = Field(
        default_factory=list,
        max_length=MAX_PLOTS,
        description="One outline per plot: [lat, lon] pairs or {lat, lng} objects (the ring closes itself)",
        examples=[[[[43.6136, 27.930], [43.6136, 27.936], [43.6100, 27.936], [43.6100, 27.930]]]],
    )
    fields: list[SelectedField] = Field(
        default_factory=list,
        max_length=MAX_PLOTS,
        description="Picked fields as the frontend holds them: {id, polygon: [{lat, lng}, ...]}",
    )

    @model_validator(mode="after")
    def check_count(self) -> "PlotsRequest":
        n = len(self.plots) + len(self.fields)
        if n == 0:
            raise ValueError("send at least one outline in `plots` or `fields`")
        if n > MAX_PLOTS:
            raise ValueError(f"at most {MAX_PLOTS} plots per request")
        return self

    def outlines(self) -> list[tuple[str | None, list[tuple[float, float]]]]:
        """(id, [(lat, lon), ...]) per plot, `plots` first, then `fields`."""
        as_pair = lambda p: (p.lat, p.lng) if isinstance(p, LatLng) else tuple(p)  # noqa: E731
        return [(None, [as_pair(p) for p in pts]) for pts in self.plots] + [
            (f.id, [(p.lat, p.lng) for p in f.polygon]) for f in self.fields
        ]


def _cache_key(kind: str, ident: str, buffer_m: float) -> str:
    # the buffer changes which pixels are read, so it is part of the key
    return f"{kind}:{ident}:b{buffer_m:g}"


def _plot_key(plot_id: str | None, points: list[tuple[float, float]], buffer_m: float) -> str:
    """Field id when given (stable across requests), else a hash of the outline."""
    if plot_id is not None:
        return _cache_key("field", plot_id, buffer_m)
    rounded = [[round(lat, 7), round(lon, 7)] for lat, lon in points]
    digest = hashlib.sha256(json.dumps(rounded).encode()).hexdigest()[:24]
    return _cache_key("outline", digest, buffer_m)


def _evaluate(
    sh: SentinelHub, inner: Rect | Polygon, years: list[int], opts: NdviOptions, cache_key: str
) -> dict:
    """One plot: the compact period list, or the full analysis with `detail`."""
    start = time.perf_counter()
    try:
        with timed(f"fetch {len(years)} years") as fetch_info:
            obs = fetch_observations(sh, inner, years, cache_key)
            fetch_info["acquisitions"] = len(obs)
    except SentinelHubError as e:
        raise HTTPException(502, str(e))
    fetched = time.perf_counter()
    params = opts.params()
    with timed("analyse"):
        result = analyse(obs, years, params)
    done = time.perf_counter()
    emit(
        "plot done", (done - start) * 1000, logging.INFO,
        fetch_ms=round((fetched - start) * 1000), analyse_ms=round((done - fetched) * 1000),
        acquisitions=len(obs), kept=result["observations_kept"], periods=len(result["summary"]),
    )
    observations, kept, daily = result.pop("observations"), result.pop("kept"), result.pop("daily")
    summary = result.pop("summary")
    if not opts.detail:
        body: dict = {"periods": summary}
        if not summary:
            body["error"] = "not enough clear Sentinel-2 images for this plot"
        return body
    return {
        "params": params.__dict__,
        "periods_summary": summary,
        **result,
        **({"observations": observations, "kept_observations": kept} if opts.include_observations else {}),
        **({"daily": daily} if opts.include_daily else {}),
    }


@router.get(
    "/ndvi",
    summary="One rectangular plot: crop / barren / dormant periods from Sentinel-2 NDVI",
)
def field_ndvi(sh: Client, rect: RectQuery, opts: Annotated[NdviOptions, Query()]):
    years = opts.years()
    plot_tag.set("rect")
    try:
        inner = shrink(rect, opts.buffer_m)
    except ValueError as e:
        raise HTTPException(422, str(e))
    key = _cache_key("rect", ",".join(f"{v:.7f}" for v in rect.bbox), opts.buffer_m)
    with timed("GET /fields/ndvi", logging.INFO, years=f"{years[0]}-{years[-1]}"):
        return _evaluate(sh, inner, years, opts, key)


@router.post(
    "/ndvi",
    summary="Several plots (outlines or picked fields): one period list per plot, in input order",
)
def plots_ndvi(sh: Client, req: PlotsRequest) -> list[dict]:
    years = req.years()
    outlines = req.outlines()
    received = time.perf_counter()

    def one(indexed: tuple[int, tuple[str | None, list[tuple[float, float]]]]) -> dict:
        i, (plot_id, points) = indexed
        plot_tag.set(f"plot {i}" + (f" {plot_id}" if plot_id is not None else ""))
        # > 0 when there are more plots than workers and this one had to wait
        emit("start", (time.perf_counter() - received) * 1000, waited="ms since request")
        head = {"id": plot_id} if plot_id is not None else {}
        try:
            with timed("outline + inward buffer", points=len(points)):
                inner = shrink_polygon(plot_polygon(points), req.buffer_m)
            key = _plot_key(plot_id, points, req.buffer_m)
            return {**head, **_evaluate(sh, inner, years, req, key)}
        except ValueError as e:
            return {**head, "periods": [], "error": str(e)}
        except HTTPException as e:
            return {**head, "periods": [], "error": e.detail}

    with timed(
        "POST /fields/ndvi", logging.INFO,
        plots=len(outlines), years=f"{years[0]}-{years[-1]}", workers=4,
    ):
        with ThreadPoolExecutor(max_workers=4) as pool_:
            return list(pool_.map(one, enumerate(outlines)))


@router.post(
    "/ndvi/prefetch",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Warm the NDVI cache for plots in the background; returns immediately",
)
def plots_ndvi_prefetch(sh: Client, req: PlotsRequest) -> dict:
    """Same body as POST /ndvi. The frontend calls this when a field is picked,
    so a later POST /ndvi finds the Copernicus data already cached (or joins
    the fetch still in flight)."""
    years = req.years()

    def warm(plot_id: str | None, points: list[tuple[float, float]]) -> None:
        plot_tag.set(f"prefetch {plot_id or 'outline'}")
        try:
            inner = shrink_polygon(plot_polygon(points), req.buffer_m)
            with timed(f"prefetch {len(years)} years", logging.INFO):
                fetch_observations(sh, inner, years, _plot_key(plot_id, points, req.buffer_m))
        except Exception as e:  # nobody is waiting on this; just log it
            log.warning("prefetch of %s failed: %s", plot_id or "outline", e)

    for plot_id, points in req.outlines():
        _prefetch_pool.submit(contextvars.copy_context().run, warm, plot_id, points)
    return {"queued": len(req.outlines()), "years": [years[0], years[-1]]}


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
