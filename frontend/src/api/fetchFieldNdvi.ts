import { SelectedField } from "../data/point"
import { API_URL } from "./apiUrl"
import { Field } from "./fetchField"

// same shape the crops page sends: the outer ring as {lat, lng} points
export function toSelectedField(field: Field): SelectedField {
    return {
        id: field.id,
        polygon: field.geometry.coordinates[0].map(([lng, lat]) => ({
            lat,
            lng,
        })),
    }
}

// default range; the backend uses the same when none is sent
export const NDVI_DEFAULT_FROM_YEAR = 2023
export const NDVI_DEFAULT_TO_YEAR = new Date().getFullYear()

function ndviBody(fields: Field[], fromYear: number, toYear: number): string {
    return JSON.stringify({
        fields: fields.map(toSelectedField),
        fromYear,
        toYear,
    })
}

// fire-and-forget: the backend starts fetching Sentinel-2 for these fields in
// the background, so later NDVI reads (the strategy context) hit its cache
export function prefetchFieldNdvi(
    fields: Field[],
    fromYear = NDVI_DEFAULT_FROM_YEAR,
    toYear = NDVI_DEFAULT_TO_YEAR,
): void {
    fetch(`${API_URL}/api/fields/ndvi/prefetch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: ndviBody(fields, fromYear, toYear),
    }).catch(() => {
        // best effort; the real request will fetch anyway
    })
}
