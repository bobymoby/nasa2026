import { API_URL } from "./apiUrl"

// ISRIC SoilGrids WMS: most probable WRB reference soil group, 250 m cells.
// Served with CORS, so the map loads it directly instead of through the backend.
const SOILGRIDS_WMS =
    "https://maps.isric.org/mapserv?map=/map/wrb.map&SERVICE=WMS&VERSION=1.3.0"

export const SOIL_TILES_URL =
    `${SOILGRIDS_WMS}&REQUEST=GetMap&LAYERS=MostProbable&STYLES=` +
    "&CRS=EPSG:3857&BBOX={bbox-epsg-3857}&WIDTH=256&HEIGHT=256" +
    "&FORMAT=image/png&TRANSPARENT=true"

export const SOIL_LEGEND_URL =
    `${SOILGRIDS_WMS}&REQUEST=GetLegendGraphic&LAYER=MostProbable` +
    "&FORMAT=image/png&SLD_VERSION=1.1.0"

export type SoilAtPoint = {
    lat: number
    lng: number
    // null over water and built-up areas
    soil: string | null
    probabilities: { soil: string; probability: number }[]
}

export async function fetchSoil(
    lat: number,
    lng: number,
    signal?: AbortSignal,
): Promise<SoilAtPoint> {
    const params = new URLSearchParams({ lat: String(lat), lng: String(lng) })
    const res = await fetch(`${API_URL}/api/soil/point?${params}`, { signal })
    if (!res.ok) {
        const body = await res.json().catch(() => null)
        throw new Error(body?.detail ?? `Failed to load soil (${res.status})`)
    }
    return res.json()
}
