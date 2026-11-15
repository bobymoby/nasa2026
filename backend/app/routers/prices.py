from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path
from pydantic import Field, field_validator

from app.routers.crops import CamelModel
from app.services.prices import pipeline
from app.services.prices.sources import PriceSourceError

router = APIRouter(prefix="/prices", tags=["prices"])

Source = Literal["eurostat", "faostat"]
Confidence = Literal["high", "medium", "low"]
CountryCode = Annotated[str, Path(min_length=2, max_length=2, description="ISO 3166 alpha-2, e.g. BG")]


class PricesRequest(CamelModel):
    country: str = Field(min_length=2, max_length=2, description="ISO 3166 alpha-2, e.g. BG", examples=["BG"])
    crops: list[str] | None = Field(None, description="Crop ids (e.g. wheat, sunflower); all mapped crops when omitted")
    overrides: dict[str, float] = Field(
        default_factory=dict, description="The farmer's own price per crop, € per tonne; replaces the reference price"
    )
    reference_years: int = Field(5, ge=1, le=15, description="Reference price = mean of this many latest years")
    include_history: bool = True

    @field_validator("overrides")
    @classmethod
    def positive_overrides(cls, v: dict[str, float]) -> dict[str, float]:
        if any(price <= 0 for price in v.values()):
            raise ValueError("override prices must be positive")
        return v


class PriceYear(CamelModel):
    year: int
    eur_per_t: float = Field(description="Today's money (priceYear euros)")
    nominal_eur_per_t: float
    source: Source
    confidence: Confidence
    flag: str | None


class Variability(CamelModel):
    cv: float = Field(description="Coefficient of variation of real prices")
    level: Literal["low", "medium", "high"]
    years: list[int]


class CropPrice(CamelModel):
    crop_id: str
    crop_name: str
    reference_price: float | None = Field(description="€ per tonne; the farmer's price when overridden")
    price_source: Literal["market", "farmer"] | None
    market_reference_price: float | None
    reference_years: list[int] | None
    confidence: Confidence | None
    variability: Variability | None
    history: list[PriceYear] | None


class PricesResponse(CamelModel):
    country: str
    country_name: str
    currency: Literal["EUR"] = "EUR"
    unit: Literal["EUR/t"] = "EUR/t"
    price_year: int = Field(description="Year whose euros all prices are expressed in")
    deflator: str = Field(description="Eurostat HICP series used, e.g. BG or EA")
    crops: list[CropPrice]
    unknown_crops: list[str]


class PriceTableRow(CamelModel):
    country: str
    crop: str
    year: int
    eur_per_t: float
    source: Source
    confidence: Confidence


def _load(country: str, crop_ids: list[str] | None) -> pipeline.CountryPrices:
    try:
        return pipeline.country_prices(country, crop_ids)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    except PriceSourceError as e:
        raise HTTPException(502, str(e)) from e


@router.post("", response_model=PricesResponse, response_model_by_alias=True,
             summary="Reference selling prices, price risk and history per crop (€/t, today's money)")
def prices(req: PricesRequest) -> PricesResponse:
    mapping = pipeline.crop_codes()
    wanted = list(dict.fromkeys((req.crops or list(mapping)) + list(req.overrides)))
    unknown = [c for c in wanted if c not in mapping]
    known = [c for c in wanted if c in mapping]
    data = _load(req.country, known)

    by_crop: dict[str, list[pipeline.PriceRow]] = {c: [] for c in known}
    for row in data.rows:
        by_crop[row.crop_id].append(row)

    crops = []
    for crop_id, rows in by_crop.items():
        override = req.overrides.get(crop_id)
        if not rows and override is None:
            continue
        s = pipeline.summarize(rows, req.reference_years, override)
        history = s.pop("history")
        crops.append(CropPrice(
            crop_id=crop_id,
            crop_name=mapping[crop_id].crop_name,
            **s,
            history=[PriceYear(year=r.year, eur_per_t=r.eur_per_t, nominal_eur_per_t=r.nominal_eur_per_t,
                               source=r.source, confidence=r.confidence, flag=r.flag)
                     for r in history] if req.include_history else None,
        ))
    return PricesResponse(
        country=data.country.code, country_name=data.country.name,
        price_year=data.price_year, deflator=data.deflator,
        crops=crops, unknown_crops=unknown,
    )


@router.get("/{country}/table", response_model=list[PriceTableRow], response_model_by_alias=True,
            summary="Every crop and year: € per tonne in today's money, with source and confidence")
def price_table(country: CountryCode) -> list[PriceTableRow]:
    data = _load(country, None)
    return [PriceTableRow(country=r.country, crop=r.crop_id, year=r.year, eur_per_t=r.eur_per_t,
                          source=r.source, confidence=r.confidence) for r in data.rows]
