from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

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
