import { API_URL } from "./apiUrl"
import { AreaSelection } from "../data/point"

export type SoilProperty = {
    unit: string
    values_by_depth: Record<string, number | null>
    sample_counts_by_depth: Record<string, number>
}

export type SoilSelection = {
    source: string
    spatial_resolution_m: number
    sampling: {
        method: string
        sample_count: number
        max_sample_count: number
        sampled_points: { lat: number; lng: number }[]
        aggregation: string
        selected_field_count?: number
    }
    depth_intervals_cm: string[]
    properties: Record<string, SoilProperty>
    note: string
}

export async function fetchSoilSelection(
    selection: AreaSelection,
    signal?: AbortSignal,
): Promise<SoilSelection> {
    const body =
        selection.kind === "bounds"
            ? { bounds: selection.bounds }
            : { fields: selection.fields }
    const res = await fetch(`${API_URL}/api/soil/selection`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal,
    })
    if (!res.ok) {
        const detail = await res.json().catch(() => null)
        throw new Error(
            detail?.detail ?? `Failed to load SoilGrids profile (${res.status})`,
        )
    }
    return res.json()
}
