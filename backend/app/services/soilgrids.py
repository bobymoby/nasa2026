"""Point properties from ISRIC SoilGrids."""

from typing import Any

import httpx

from app.cache import cached_json

SOILGRIDS_PROPERTIES_URL = "https://rest.isric.org/soilgrids/v2.0/properties/query"
DEPTH_INTERVALS = (
    "0-5cm",
    "5-15cm",
    "15-30cm",
    "30-60cm",
    "60-100cm",
    "100-200cm",
)

CHEMISTRY_PROPERTIES = {
    "phh2o": ("pH_water", "pH"),
    "nitrogen": ("nitrogen", "g/kg"),
    "soc": ("organic_carbon", "g/kg"),
}
PHYSICAL_PROPERTIES = {
    "clay": ("clay", "%"),
    "sand": ("sand", "%"),
    "silt": ("silt", "%"),
    "bdod": ("bulk_density", "kg/dm3"),
}


class SoilGridsError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _property_values(
    layers: list[dict[str, Any]], property_name: str, output_unit: str
) -> dict[str, Any]:
    layer = next((item for item in layers if item.get("name") == property_name), None)
    if layer is None:
        raise SoilGridsError(f"SoilGrids response is missing {property_name}")

    try:
        factor = float(layer["unit_measure"]["d_factor"])
        depths = layer["depths"]
    except (KeyError, TypeError, ValueError) as exc:
        raise SoilGridsError(f"Malformed SoilGrids response for {property_name}") from exc
    if factor <= 0:
        raise SoilGridsError(f"Invalid SoilGrids scale factor for {property_name}")

    values_by_depth = {}
    for label in DEPTH_INTERVALS:
        depth = next((item for item in depths if item.get("label") == label), None)
        value = depth.get("values", {}).get("mean") if depth else None
        try:
            values_by_depth[label] = round(float(value) / factor, 3) if value is not None else None
        except (TypeError, ValueError) as exc:
            raise SoilGridsError(
                f"Malformed SoilGrids value for {property_name} at {label}"
            ) from exc

    return {"unit": output_unit, "values_by_depth": values_by_depth}


def _fetch_properties(lat: float, lng: float, property_names: tuple[str, ...]) -> dict:
    params = [
        ("lon", lng),
        ("lat", lat),
        *(("property", name) for name in property_names),
        *(("depth", depth) for depth in DEPTH_INTERVALS),
        ("value", "mean"),
    ]
    try:
        response = httpx.get(SOILGRIDS_PROPERTIES_URL, params=params, timeout=60)
    except httpx.HTTPError as exc:
        raise SoilGridsError(f"SoilGrids request failed: {exc}") from exc
    if response.status_code == 429:
        raise SoilGridsError("SoilGrids rate limit reached; try again later", 429)
    if response.status_code != 200:
        raise SoilGridsError(f"SoilGrids returned HTTP {response.status_code}", 502)
    try:
        data = response.json()
        layers = data["properties"]["layers"]
    except (ValueError, KeyError, TypeError) as exc:
        raise SoilGridsError("Malformed SoilGrids response") from exc
    if not isinstance(layers, list):
        raise SoilGridsError("Malformed SoilGrids response: layers must be a list")
    return {"layers": layers}


def _cached_properties(lat: float, lng: float, property_names: tuple[str, ...]) -> list:
    key = {"lat": lat, "lng": lng, "properties": property_names}
    result = cached_json(
        "soilgrids_properties",
        key,
        lambda: _fetch_properties(lat, lng, property_names),
    )
    return result["layers"]


def _format_properties(
    layers: list[dict[str, Any]], mapping: dict[str, tuple[str, str]]
) -> dict[str, Any]:
    return {
        public_name: _property_values(layers, source_name, unit)
        for source_name, (public_name, unit) in mapping.items()
    }


def get_soil_chemistry(lat: float, lng: float) -> dict[str, Any]:
    names = tuple(CHEMISTRY_PROPERTIES)
    layers = _cached_properties(lat, lng, names)
    return {
        "source": "ISRIC SoilGrids",
        "spatial_resolution_m": 250,
        "location": {"lat": lat, "lng": lng},
        "properties": _format_properties(layers, CHEMISTRY_PROPERTIES),
    }


def get_soil_physical(lat: float, lng: float) -> dict[str, Any]:
    names = tuple(PHYSICAL_PROPERTIES)
    layers = _cached_properties(lat, lng, names)
    return {
        "source": "ISRIC SoilGrids",
        "spatial_resolution_m": 250,
        "location": {"lat": lat, "lng": lng},
        "depth_intervals_cm": list(DEPTH_INTERVALS),
        "properties": _format_properties(layers, PHYSICAL_PROPERTIES),
        "note": (
            "Depth intervals describe the SoilGrids profile layers; they are not "
            "a measurement of effective soil depth."
        ),
    }
