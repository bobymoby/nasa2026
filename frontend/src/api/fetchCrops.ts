import { AreaSelection } from "../data/point"
import { API_URL } from "./apiUrl"

// how the crop affects the soil, from crop_soil_scores
export type SoilImpact = {
    crop: string
    // 0..100
    score: number | null
    nitrogenAdded: number | null // 0..3
    organicMatter: number | null // 0..3
    soilCover: number | null // 0..3
    soilDisturbance: number | null // -3..0
    breakCrop: number | null // 0..2
    removalCompaction: number | null // -2..0
    family: string | null
    note: string | null
}

// id is the crop_soil_scores key, e.g. "winter_common_soft_wheat"
export type Crop = { id: string; name: string; soil: SoilImpact }

export async function fetchCrops(
    selection: AreaSelection,
    signal?: AbortSignal,
): Promise<Crop[]> {
    const [path, body] =
        selection.kind === "bounds"
            ? ["bounds", selection.bounds]
            : ["fields", { fields: selection.fields }]
    const res = await fetch(`${API_URL}/api/crops/${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal,
    })
    if (!res.ok) throw new Error(`Failed to load crops (${res.status})`)
    const data: { crops: Crop[] } = await res.json()
    return data.crops
}
