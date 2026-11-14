from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from app.services.crop_suitability import (
    ClimateFeatures,
    SoilFeatures,
    suitable_crops,
    texture_class,
)

router = APIRouter(tags=["crops"])


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class Point(CamelModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class Bounds(CamelModel):
    top_left: Point
    bottom_right: Point

    @model_validator(mode="after")
    def check_corners(self) -> "Bounds":
        if self.top_left.lat <= self.bottom_right.lat:
            raise ValueError("topLeft.lat must be greater than bottomRight.lat")
        if self.top_left.lng >= self.bottom_right.lng:
            raise ValueError("topLeft.lng must be less than bottomRight.lng")
        return self


class SelectedField(CamelModel):
    id: str
    # outer ring of the field; may repeat the first point at the end
    polygon: list[Point] = Field(min_length=3)


class FieldsRequest(CamelModel):
    fields: list[SelectedField] = Field(min_length=1, max_length=10_000)


class Crop(CamelModel):
    id: str
    name: str


class CropsResponse(CamelModel):
    crops: list[Crop]


# TODO: replace with real logic based on the selected area
STUB_CROPS = [
    Crop(id="wheat", name="Wheat"),
    Crop(id="maize", name="Maize"),
    Crop(id="sunflower", name="Sunflower"),
    Crop(id="barley", name="Barley"),
    Crop(id="rapeseed", name="Rapeseed"),
]


@router.post(
    "/crops/bounds",
    response_model=CropsResponse,
    response_model_by_alias=True,
    summary="Crops available in a rectangle",
)
def crops_for_bounds(bounds: Bounds) -> CropsResponse:
    return CropsResponse(crops=STUB_CROPS)


@router.post(
    "/crops/fields",
    response_model=CropsResponse,
    response_model_by_alias=True,
    summary="Crops available on a set of selected fields",
)
def crops_for_fields(selection: FieldsRequest) -> CropsResponse:
    return CropsResponse(crops=STUB_CROPS)


# ------------------------------------------------------------ suitability

class SoilInput(CamelModel):
    """Every field is optional; unknown factors are not scored."""

    ph: float | None = Field(default=None, ge=0, le=14, description="pH in water")
    texture: Literal["light", "medium", "heavy", "organic"] | None = Field(
        default=None, description="If omitted, derived from sandPct and clayPct"
    )
    sand_pct: float | None = Field(default=None, ge=0, le=100)
    clay_pct: float | None = Field(default=None, ge=0, le=100)
    depth_cm: float | None = Field(default=None, ge=0, description="Rootable soil depth")
    drainage: Literal["poor", "well", "excessive"] | None = None
    salinity_ds_m: float | None = Field(
        default=None, ge=0, description="Electrical conductivity, dS/m"
    )
    fertility: Literal["low", "moderate", "high"] | None = None

    @model_validator(mode="after")
    def check_fractions(self) -> "SoilInput":
        if (self.sand_pct is None) != (self.clay_pct is None):
            raise ValueError("sandPct and clayPct must be given together")
        if self.sand_pct is not None and self.sand_pct + self.clay_pct > 100:
            raise ValueError("sandPct + clayPct must be ≤ 100")
        return self


class ClimateInput(CamelModel):
    """Monthly climatology, e.g. averaged /api/climate/monthly rows."""

    temperature: list[float] = Field(
        min_length=12, max_length=12, description="Mean air temperature, °C, January first"
    )
    precipitation: list[float] = Field(
        min_length=12, max_length=12, description="Precipitation, mm/month, January first"
    )
    min_temperature: list[float] | None = Field(
        default=None,
        min_length=12,
        max_length=12,
        description="Mean daily minimum temperature, °C; enables frost checks",
    )

    @model_validator(mode="after")
    def check_precipitation(self) -> "ClimateInput":
        if any(p < 0 for p in self.precipitation):
            raise ValueError("precipitation must not be negative")
        return self


class SuitabilityRequest(CamelModel):
    soil: SoilInput = Field(default_factory=SoilInput)
    climate: ClimateInput
    min_score: float = Field(default=0.5, ge=0, le=1)
    irrigated: bool = Field(default=False, description="Ignore rainfall")
    categories: list[str] | None = Field(
        default=None,
        description='Keep crops whose ECOCROP category contains any of these, e.g. ["cereals", "pulses"]',
    )
    exclude_categories: list[str] = Field(default_factory=lambda: ["weed"])
    life_span: Literal["annual", "biennial", "perennial"] | None = None
    limit: int | None = Field(default=50, ge=1, description="null = no limit")


class SuitableCrop(CamelModel):
    scientific_name: str
    common_names: list[str]
    family: str | None
    category: str | None
    life_span: str | None
    score: float = Field(description="0..1, the worst factor score")
    limiting: list[str] = Field(description="Factors that set the score")
    factors: dict[str, float | None] = Field(description="null = not scored")
    growing_months: list[int] = Field(description="Best growing window, 1 = January")


class SuitabilityResponse(CamelModel):
    texture: str | None = Field(description="Soil texture class used")
    count: int
    crops: list[SuitableCrop]


@router.post(
    "/crops/suitable",
    response_model=SuitabilityResponse,
    response_model_by_alias=True,
    summary="Plants suited to the given soil and climate (FAO ECOCROP)",
)
def crops_suitable(request: SuitabilityRequest) -> SuitabilityResponse:
    """Scores every ECOCROP plant against the soil and monthly climate in the
    body; nothing is fetched. Each factor scores 0..1 and the crop's score is
    the worst one. Unknown inputs are not scored, so send what you have."""
    soil_in = request.soil
    texture = soil_in.texture
    if texture is None and soil_in.sand_pct is not None:
        texture = texture_class(soil_in.sand_pct, soil_in.clay_pct)

    soil = SoilFeatures(
        ph=soil_in.ph,
        texture=texture,
        depth_cm=soil_in.depth_cm,
        drainage=soil_in.drainage,
        salinity_ds_m=soil_in.salinity_ds_m,
        fertility=soil_in.fertility,
    )
    climate = ClimateFeatures(
        temperature=request.climate.temperature,
        precipitation=request.climate.precipitation,
        min_temperature=request.climate.min_temperature,
    )
    results = suitable_crops(
        soil,
        climate,
        min_score=request.min_score,
        irrigated=request.irrigated,
        categories=request.categories,
        exclude_categories=request.exclude_categories,
        life_span=request.life_span,
        limit=request.limit,
    )
    crops = [
        SuitableCrop(
            **{k: v for k, v in r.items() if k not in ("limiting", "factors")},
            limiting=[to_camel(f) for f in r["limiting"]],
            factors={to_camel(k): v for k, v in r["factors"].items()},
        )
        for r in results
    ]
    return SuitabilityResponse(texture=texture, count=len(crops), crops=crops)
