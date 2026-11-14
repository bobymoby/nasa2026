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

// the layer's fill colours, read off its GetLegendGraphic image; GetMap
// paints with exactly these, so a pixel's colour identifies its soil group
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

const CLASS_BY_RGB = new Map(
    SOIL_CLASSES.map((c) => [parseInt(c.color.slice(1), 16), c]),
)

// [west, south, east, north] in degrees
export type LngLatBox = [number, number, number, number]

export type SoilShare = SoilClass & { share: number }

const EARTH_HALF_CIRCUMFERENCE = 20037508.34
const MAX_MERCATOR_LAT = 85.0511
// longest side of the sampled image; 250 m cells don't need more
const SAMPLE_MAX_SIDE = 384

function toMercator(lng: number, lat: number): [number, number] {
    const clamped = Math.max(-MAX_MERCATOR_LAT, Math.min(MAX_MERCATOR_LAT, lat))
    const x = (lng / 180) * EARTH_HALF_CIRCUMFERENCE
    const y =
        (Math.log(Math.tan(((90 + clamped) * Math.PI) / 360)) / Math.PI) *
        EARTH_HALF_CIRCUMFERENCE
    return [x, y]
}

// renders the soil layer for the box and counts pixels per soil group, so
// the legend lists only the groups in view, largest share first
export async function fetchVisibleSoilClasses(
    [west, south, east, north]: LngLatBox,
    signal?: AbortSignal,
): Promise<SoilShare[]> {
    const [minX, minY] = toMercator(west, south)
    const [maxX, maxY] = toMercator(east, north)
    const scale = SAMPLE_MAX_SIDE / Math.max(maxX - minX, maxY - minY)
    const width = Math.max(1, Math.round((maxX - minX) * scale))
    const height = Math.max(1, Math.round((maxY - minY) * scale))

    const url =
        `${SOIL_GETMAP}&BBOX=${minX},${minY},${maxX},${maxY}` +
        `&WIDTH=${width}&HEIGHT=${height}`
    const res = await fetch(url, { signal })
    if (!res.ok) throw new Error(`Failed to load soil map (${res.status})`)
    const bitmap = await createImageBitmap(await res.blob())

    const canvas = document.createElement("canvas")
    canvas.width = bitmap.width
    canvas.height = bitmap.height
    const ctx = canvas.getContext("2d", { willReadFrequently: true })
    if (!ctx) throw new Error("Canvas is not supported")
    ctx.drawImage(bitmap, 0, 0)
    bitmap.close()
    const { data } = ctx.getImageData(0, 0, canvas.width, canvas.height)

    const counts = new Map<SoilClass, number>()
    let total = 0
    for (let i = 0; i < data.length; i += 4) {
        // transparent = water, built-up or no data
        if (data[i + 3] < 128) continue
        const soil = CLASS_BY_RGB.get(
            (data[i] << 16) | (data[i + 1] << 8) | data[i + 2],
        )
        if (!soil) continue
        counts.set(soil, (counts.get(soil) ?? 0) + 1)
        total++
    }

    return Array.from(counts, ([soil, count]) => ({
        ...soil,
        share: count / total,
    })).sort((a, b) => b.share - a.share)
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
