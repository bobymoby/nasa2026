import { Bounds } from "./point"

const API_URL = process.env.REACT_APP_API_URL ?? "http://localhost:8000"

export type Crop = { id: string; name: string }

export async function fetchCrops(
    bounds: Bounds,
    signal?: AbortSignal,
): Promise<Crop[]> {
    const res = await fetch(`${API_URL}/api/crops`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(bounds),
        signal,
    })
    if (!res.ok) throw new Error(`Failed to load crops (${res.status})`)
    const data: { crops: Crop[] } = await res.json()
    return data.crops
}
