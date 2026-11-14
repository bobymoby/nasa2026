The soil-characteristics endpoint uses the Sentinel Hub Statistical API to calculate
indices on the service instead of downloading full satellite images. It requires the
Sentinel Hub OAuth client credentials above and works with the configured Sentinel Hub
deployment. Sentinel-2 L2A reflectance bands are supplied in reflectance units by the
service; the Evalscript masks invalid SCL classes and calculates the indices per pixel
before the API returns date-level statistics.

## Soil indicators: what the endpoint returns

Request `GET /api/sentinel/soil-characteristics` with the field rectangle and inclusive
date range. The corner coordinates are latitude,longitude:

```powershell
$uri = "http://localhost:8000/api/sentinel/soil-characteristics?top_left=43.60%2C27.80&bottom_right=43.598%2C27.802&from=2024-06-01&to=2024-06-07"
$result = Invoke-RestMethod -Uri $uri
$result | ConvertTo-Json -Depth 8
```

The endpoint needs `SH_CLIENT_ID`, `SH_CLIENT_SECRET`, and `SH_DEPLOYMENT` configured
as described above. It requests Sentinel-2 Level-2A surface reflectance through the
Sentinel Hub Statistical API. Instead of sending full satellite images to this app, the
service calculates the indices for pixels on Sentinel Hub and returns their statistics.
Historical completed date ranges are cached in `backend/.cache/`; a first request for a
range still has to be processed by the upstream service.

### Measurements and indicators

The endpoint's “soil characteristics” are indirect satellite indicators. Satellites
measure reflected light, not soil chemistry. The values can also describe vegetation
cover over the soil, so use them as clues about surface/plant conditions rather than
laboratory soil tests.

| Response field                    | Plain-language meaning                                                                                                                                                              | How it is calculated                                                         |
| --------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| `vegetation_condition.ndvi`       | Greenness / density of live vegetation. Higher values commonly mean more green cover; low values can mean bare ground, sparse plants, or dormant vegetation.                        | `(B08 - B04) / (B08 + B04)`                                                  |
| `vegetation_condition.savi`       | A vegetation index designed to reduce the influence of visible soil between plants.                                                                                                 | `1.5 * (B08 - B04) / (B08 + B04 + 0.5)`                                      |
| `water_stress.ndmi`               | Moisture-related signal from vegetation and the land surface. It is not a direct measurement of soil moisture or a drought diagnosis.                                               | `(B08 - B11) / (B08 + B11)`                                                  |
| `soil_exposure.bsi`               | Bare-soil exposure signal. A higher value often indicates more exposed bright soil, but other surfaces and conditions can affect it.                                                | `(B11 + B04 - B08 - B02) / (B11 + B04 + B08 + B02)`                          |
| `soil_exposure.bare_soil_percent` | Share of valid pixels classified by Sentinel-2 as “not vegetated” (SCL class 5). It is a classification-based estimate, not a guarantee that every such pixel is agricultural soil. | Class-5 valid pixels divided by all valid pixels, expressed as a percentage. |
| `chlorophyll_indicator.ndre`      | Red-edge vegetation signal associated with plant chlorophyll and canopy condition. It is not a direct chlorophyll measurement.                                                      | `(B08 - B05) / (B08 + B05)`                                                  |

`B02`, `B04`, `B05`, `B08`, and `B11` are Sentinel-2 blue, red, red-edge, near
infrared, and short-wave infrared bands. The service uses surface reflectance values
and calculates the formulas per pixel; it does not estimate pH, nitrogen, phosphorus,
potassium, organic carbon, soil texture, or yield.

### Pixels included

The service uses the Sentinel-2 Scene Classification Layer (SCL) and data mask. Only
SCL class 4 (vegetation) and class 5 (not vegetated) are kept. No-data, saturated or
defective, dark, cloud-shadow, water, unclassified, cloud, cirrus, and snow classes are
excluded. Pixels with non-positive/non-finite reflectance or invalid formula
denominators are also excluded. Indices are calculated from Sentinel Hub's
reflectance-valued inputs, which avoids mixing the bands' native integer storage
scales. The Statistical API aligns the requested inputs on its analysis grid.

### Summary and time series

- The grouped index fields (`ndvi`, `savi`, `ndmi`, `bsi`, and `ndre`) are means of the
  daily interval means that had usable pixels. They are rounded to four decimal places.
- `bare_soil_percent` in the summary is weighted by the number of valid pixels in each
  usable daily interval, and is rounded to one decimal place.
- `time_series` contains one row per usable daily interval, with `date` and all five
  index means. It does not currently include a per-date bare-soil percentage.
- `observations_found` counts daily intervals with samples, including intervals where
  all pixels were masked. `observations_used` counts intervals with at least one valid
  pixel. These are intervals containing Sentinel-2 data, not counts of individual
  satellite product files or overpasses.
- `valid_observations_percent` is `observations_used / observations_found * 100`. It
  describes the share of intervals usable for analysis, not the percentage of clear
  pixels within the field. If no intervals have samples, it is `0.0`.
- Missing values are returned as `null`; an empty `time_series` means no usable
  observations were found for the area and dates.

### Historical trend labels

`historical_changes` reports `increasing`, `decreasing`, `stable`, or
`insufficient_data` for NDVI, NDMI, and bare-soil percentage. A trend is calculated
only when there are observations in at least three distinct years within the same
31-day calendar window. The calculation averages values within each year and fits a
linear trend across those yearly averages. It is a broad historical comparison, not a
forecast.

The endpoint does not know crop type or growth stage, so matching calendar dates do not
guarantee comparable crop stages. Treat trend labels cautiously where crop rotation,
planting dates, or land use changed. When there is not enough same-season data, the
label is `insufficient_data`; details about the years and observations used are in
`trend_basis`.
