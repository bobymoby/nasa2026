import { API_URL } from "./apiUrl"

// lng/lat extent of the image, the same one Sentinel Hub renders
export type ImageBBox = { west: number; south: number; east: number; north: number }

// "ndvi-color" is NDVI rendered as colours, with clouds left transparent
export type SentinelImageType = "truecolor" | "ndvi-color"

export type SentinelImageRequest = {
    bbox: ImageBBox
    type: SentinelImageType
    from: string // YYYY-MM-DD
    to: string
    width: number
    height: number
    maxCloud?: number
}

// Sentinel-2 L2A PNG, least-cloudy mosaic over the date range
export async function fetchSentinelImage(
    { bbox, type, from, to, width, height, maxCloud = 30 }: SentinelImageRequest,
    signal?: AbortSignal,
): Promise<Blob> {
    const params = new URLSearchParams({
        top_left: `${bbox.north},${bbox.west}`,
        bottom_right: `${bbox.south},${bbox.east}`,
        from,
        to,
        type,
        format: "png",
        width: String(width),
        height: String(height),
        max_cloud: String(maxCloud),
    })
    const res = await fetch(`${API_URL}/api/sentinel/image?${params}`, {
        signal,
    })
    if (!res.ok) {
        throw new Error(`Failed to load Sentinel-2 image (${res.status})`)
    }
    return res.blob()
}
