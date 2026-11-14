import { SelectedField } from "../data/point"
import { API_URL } from "./apiUrl"
import { Field } from "./fetchField"

export type PeriodType =
    | "barren"
    | "crop"
    | "dormant_crop"
    | "green_cover"
    | "uncertain"

export type CropType = "winter" | "summer" | "winter_or_spring" | "unknown"

export type NdviPeriod = {
    start: string // ISO date, inclusive
    end: string // ISO date, inclusive
    type: PeriodType
    crop_type: CropType | null
    confidence: "high" | "medium" | "low"
}

// one per field, in request order; `error` when that field couldn't be analysed
export type FieldNdvi = {
    id?: string
    periods: NdviPeriod[]
    error?: string
}

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

export async function fetchFieldNdvi(
    fields: Field[],
    fromYear: number,
    toYear: number,
    signal?: AbortSignal,
): Promise<FieldNdvi[]> {
    const res = await fetch(`${API_URL}/api/fields/ndvi`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            fields: fields.map(toSelectedField),
            fromYear,
            toYear,
        }),
        signal,
    })
    if (!res.ok) {
        const detail = await res.text().catch(() => "")
        throw new Error(`NDVI request failed (${res.status}) ${detail}`)
    }
    return res.json()
}
