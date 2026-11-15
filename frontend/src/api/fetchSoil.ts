import { API_URL } from "./apiUrl"

// ISRIC SoilGrids WMS: most probable WRB reference soil group, 250 m cells.
// Served with CORS, so the map loads it directly instead of through the backend.
const SOILGRIDS_WMS =
    "https://maps.isric.org/mapserv?map=/map/wrb.map&SERVICE=WMS&VERSION=1.3.0"

const SOIL_GETMAP =
    `${SOILGRIDS_WMS}&REQUEST=GetMap&LAYERS=MostProbable&STYLES=` +
    "&CRS=EPSG:3857&FORMAT=image/png&TRANSPARENT=true"

export const SOIL_TILES_URL =
    `${SOIL_GETMAP}&BBOX={bbox-epsg-3857}&WIDTH=256&HEIGHT=256`

export type SoilClass = { name: string; color: string }

// the layer's fill colours, read off its GetLegendGraphic image
export const SOIL_CLASSES: SoilClass[] = [
    { name: "Acrisols", color: "#f7991d" },
    { name: "Albeluvisols", color: "#9b9d57" },
    { name: "Alisols", color: "#faf7c0" },
    { name: "Andosols", color: "#ed3a33" },
    { name: "Arenosols", color: "#f7d8ac" },
    { name: "Calcisols", color: "#ffee00" },
    { name: "Cambisols", color: "#fecd67" },
    { name: "Chernozems", color: "#e2c837" },
    { name: "Cryosols", color: "#756a92" },
    { name: "Durisols", color: "#efe6bf" },
    { name: "Ferralsols", color: "#f6872d" },
    { name: "Fluvisols", color: "#01b0ef" },
    { name: "Gleysols", color: "#9291b9" },
    { name: "Gypsisols", color: "#fbf6a5" },
    { name: "Histosols", color: "#8b898a" },
    { name: "Kastanozems", color: "#c99580" },
    { name: "Leptosols", color: "#d5d6d8" },
    { name: "Lixisols", color: "#f9bdbf" },
    { name: "Luvisols", color: "#f48385" },
    { name: "Nitisols", color: "#f7a082" },
    { name: "Phaeozems", color: "#ba6850" },
    { name: "Planosols", color: "#f59354" },
    { name: "Plinthosols", color: "#6f0e41" },
    { name: "Podzols", color: "#0daf63" },
    { name: "Regosols", color: "#ffe2ae" },
    { name: "Solonchaks", color: "#ed3994" },
    { name: "Solonetz", color: "#f4cde2" },
    { name: "Stagnosols", color: "#40c1eb" },
    { name: "Umbrisols", color: "#618f82" },
    { name: "Vertisols", color: "#9e567c" },
]

const CLASS_BY_NAME = new Map(SOIL_CLASSES.map((c) => [c.name, c]))

// [west, south, east, north] in degrees
export type LngLatBox = [number, number, number, number]

export type SoilShare = SoilClass & { share: number }

// counted by the backend from the stored SoilGrids tiles, Bulgaria only
export async function fetchVisibleSoilClasses(
    [west, south, east, north]: LngLatBox,
    signal?: AbortSignal,
): Promise<SoilShare[]> {
    const params = new URLSearchParams({
        top_left: `${north},${west}`,
        bottom_right: `${south},${east}`,
    })
    const res = await fetch(`${API_URL}/api/soil/legend?${params}`, { signal })
    if (!res.ok) {
        const body = await res.json().catch(() => null)
        throw new Error(
            body?.detail ?? `Failed to load soil legend (${res.status})`,
        )
    }
    const shares: { soil: string; share: number }[] = await res.json()
    return shares.flatMap(({ soil, share }) => {
        const soilClass = CLASS_BY_NAME.get(soil)
        return soilClass ? [{ ...soilClass, share }] : []
    })
}

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
