from pydantic import BaseModel, Field


class MonthWeather(BaseModel):
    """One calendar month; a field is None when ERA5 has no data for it.

    Each `*_std` is the sample standard deviation of that month's daily
    values (day-to-day variability), in the field's daily unit.
    """

    month: str = Field(description="YYYY-MM")
    temperature: float | None = Field(description="Mean air temperature at 2 m, °C")
    temperature_std: float | None = Field(description="Std of daily mean temperature, °C")
    max_temperature: float | None = Field(description="Mean of daily maximum temperature, °C")
    max_temperature_std: float | None = Field(description="Std of daily maximum temperature, °C")
    min_temperature: float | None = Field(description="Mean of daily minimum temperature, °C")
    min_temperature_std: float | None = Field(description="Std of daily minimum temperature, °C")
    precipitation: float | None = Field(description="Total precipitation, mm/month")
    precipitation_std: float | None = Field(description="Std of daily precipitation, mm/day")
    humidity: float | None = Field(description="Mean relative humidity at 2 m, %")
    humidity_std: float | None = Field(description="Std of daily mean relative humidity, %")
    solar_radiation: float | None = Field(
        description="Mean daily shortwave radiation, MJ/m²/day"
    )
    solar_radiation_std: float | None = Field(
        description="Std of daily shortwave radiation, MJ/m²/day"
    )
    wind_speed: float | None = Field(description="Mean wind speed at 10 m, m/s")
    wind_speed_std: float | None = Field(description="Std of daily mean wind speed, m/s")


class MonthlyWeather(BaseModel):
    source: str
    rect: dict
    points: list[tuple[float, float]] = Field(description="ERA5 grid points averaged (lat, lon)")
    from_year: int
    to_year: int
    months: list[MonthWeather]
