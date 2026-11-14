import { API_URL } from "./apiUrl"

// vector tiles for every field; layer "fields", properties field_id + crop_category
export const FIELD_TILES_URL = `${API_URL}/api/fields/tiles/{z}/{x}/{y}.pbf`
export const FIELD_TILES_LAYER = "fields"
// the backend returns empty tiles below this zoom
export const FIELD_TILES_MIN_ZOOM = 10

export type FieldProperties = {
    field_id: string
    farm_id: string | null
    crop_code: string | null
    crop_name: string | null
    crop_name_en: string | null
    crop_category: string | null
    crop_category_code: number | null
    organic: number | null
    field_size: number | null
    area_ha: number
}

export type Polygon = { type: "Polygon"; coordinates: number[][][] }

export type Field = {
    type: "Feature"
    id: string
    geometry: Polygon
    properties: FieldProperties
}

export async function fetchField(
    fieldId: string,
    signal?: AbortSignal,
): Promise<Field> {
    const res = await fetch(
        `${API_URL}/api/fields/${encodeURIComponent(fieldId)}`,
        { signal },
    )
    if (!res.ok) throw new Error(`Failed to load field (${res.status})`)
    return res.json()
}
