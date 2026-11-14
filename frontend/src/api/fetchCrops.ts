import { AreaSelection } from "../data/point"
import { API_URL } from "./apiUrl"

export type Crop = { id: string; name: string }

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
