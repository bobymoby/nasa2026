import csv
import io
from functools import lru_cache
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.config import settings
from app.services.sentinel_hub import (
    EVAL_NDVI_STATS,
    EVALSCRIPTS,
    SentinelHub,
    SentinelHubError,
    summarize_ndvi_stats,
)

router = APIRouter(prefix="/sentinel", tags=["sentinel"])

IMAGE_FORMATS = {"tiff": "image/tiff", "png": "image/png", "jpeg": "image/jpeg"}


@lru_cache
def _client() -> SentinelHub:
    return SentinelHub(
        settings.sh_client_id, settings.sh_client_secret, settings.sh_deployment
    )


def get_client() -> SentinelHub:
    if not settings.sh_client_id or not settings.sh_client_secret:
        raise HTTPException(503, "SH_CLIENT_ID and SH_CLIENT_SECRET are not configured")
    return _client()


Client = Annotated[SentinelHub, Depends(get_client)]


def parse_bbox(
    bbox: Annotated[
        str,
        Query(
            description="min_lon,min_lat,max_lon,max_lat (WGS84)",
            examples=["23.2,42.6,23.4,42.75"],
        ),
    ],
) -> list[float]:
    try:
        vals = [float(v) for v in bbox.split(",")]
    except ValueError:
        raise HTTPException(422, "bbox must be four comma-separated numbers")
    if len(vals) != 4:
        raise HTTPException(422, "bbox must be four comma-separated numbers")
    return vals


Bbox = Annotated[list[float], Depends(parse_bbox)]
DateFrom = Annotated[str, Query(alias="from", description="YYYY-MM-DD")]
DateTo = Annotated[str, Query(alias="to", description="YYYY-MM-DD")]
Collection = Annotated[str, Query()]
MaxCloud = Annotated[float | None, Query(ge=0, le=100)]


def _upstream(e: SentinelHubError) -> HTTPException:
    return HTTPException(502, str(e))


@router.get("/search", summary="List available scenes (Catalog API)")
def search(
    sh: Client,
    bbox: Bbox,
    t_from: DateFrom,
    t_to: DateTo,
    collection: Collection = "sentinel-2-l2a",
    max_cloud: MaxCloud = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    raw: bool = False,
):
    try:
        feats = sh.search(bbox, t_from, t_to, collection, max_cloud, limit)
    except SentinelHubError as e:
        raise _upstream(e)
    if raw:
        return feats
    return [
        {
            "id": f["id"],
            "datetime": f["properties"].get("datetime"),
            "cloud_cover": f["properties"].get("eo:cloud_cover"),
        }
        for f in feats
    ]


@router.get(
    "/image",
    summary="Download an image (Process API)",
    response_class=Response,
    responses={200: {"content": {m: {} for m in IMAGE_FORMATS.values()}}},
)
def image(
    sh: Client,
    bbox: Bbox,
    t_from: DateFrom,
    t_to: DateTo,
    type: Literal["truecolor", "ndvi"] = "truecolor",
    format: Literal["tiff", "png", "jpeg"] = "tiff",
    width: Annotated[int, Query(ge=1, le=2500)] = 512,
    height: Annotated[int, Query(ge=1, le=2500)] = 512,
    collection: Collection = "sentinel-2-l2a",
    max_cloud: MaxCloud = None,
):
    """NDVI is FLOAT32; use `format=tiff` to keep real values."""
    fmt = IMAGE_FORMATS[format]
    try:
        data = sh.image(
            bbox,
            t_from,
            t_to,
            EVALSCRIPTS[type],
            fmt,
            width,
            height,
            collection,
            max_cloud if max_cloud is not None else 100,
        )
    except SentinelHubError as e:
        raise _upstream(e)
    return Response(content=data, media_type=fmt)


@router.get("/stats", summary="NDVI time series (Statistical API)")
def stats(
    sh: Client,
    bbox: Bbox,
    t_from: DateFrom,
    t_to: DateTo,
    interval: Annotated[
        str, Query(description="ISO-8601 duration, e.g. P1D, P10D, P1M")
    ] = "P10D",
    collection: Collection = "sentinel-2-l2a",
    max_cloud: MaxCloud = None,
    format: Literal["json", "csv"] = "json",
):
    try:
        raw = sh.stats(
            bbox,
            t_from,
            t_to,
            EVAL_NDVI_STATS,
            interval,
            collection=collection,
            max_cloud=max_cloud if max_cloud is not None else 100,
        )
    except SentinelHubError as e:
        raise _upstream(e)
    rows = summarize_ndvi_stats(raw)
    if format == "csv":
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=list(rows[0]) if rows else ["from"])
        w.writeheader()
        w.writerows(rows)
        return Response(
            buf.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="ndvi.csv"'},
        )
    return rows
