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


@router.post("/crops", response_model=CropsResponse, response_model_by_alias=True)
def available_crops(bounds: Bounds) -> CropsResponse:
    return CropsResponse(crops=STUB_CROPS)
