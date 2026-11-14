import "./SentinelPanel.css"
import { useEffect, useMemo, useState } from "react"

import { Field } from "../../api/fetchField"
import { fetchSentinelImage, ImageBBox } from "../../api/fetchSentinelImage"

// wait for the selection to settle before asking Sentinel Hub (each request
// costs quota and takes 1-3 s)
const DEBOUNCE_MS = 1000
const LOOKBACK_DAYS = 90
// longest image side in px; Sentinel-2 is 10 m/px, so small fields get upsampled
const IMAGE_MAX_SIDE = 512
// margin around the fields, as a share of the box size, and at least ~100 m
const PADDING_RATIO = 0.1
const MIN_PADDING_DEG = 0.001

function fieldsBBox(fields: Field[]): ImageBBox {
    let west = Infinity
    let south = Infinity
    let east = -Infinity
    let north = -Infinity
    for (const { geometry } of fields) {
        for (const [lng, lat] of geometry.coordinates[0]) {
            west = Math.min(west, lng)
            east = Math.max(east, lng)
            south = Math.min(south, lat)
            north = Math.max(north, lat)
        }
    }
    const padLng = Math.max((east - west) * PADDING_RATIO, MIN_PADDING_DEG)
    const padLat = Math.max((north - south) * PADDING_RATIO, MIN_PADDING_DEG)
    return {
        west: west - padLng,
        south: south - padLat,
        east: east + padLng,
        north: north + padLat,
    }
}

// pixel size that keeps the box's real-world aspect ratio
function imageSize({ west, south, east, north }: ImageBBox) {
    const midLat = ((north + south) / 2) * (Math.PI / 180)
    const widthM = (east - west) * Math.cos(midLat)
    const heightM = north - south
    const scale = IMAGE_MAX_SIDE / Math.max(widthM, heightM)
    return {
        width: Math.max(16, Math.round(widthM * scale)),
        height: Math.max(16, Math.round(heightM * scale)),
    }
}

function isoDate(date: Date): string {
    return date.toISOString().slice(0, 10)
}

function dateRange() {
    const to = new Date()
    const from = new Date(to)
    from.setDate(from.getDate() - LOOKBACK_DAYS)
    return { from: isoDate(from), to: isoDate(to) }
}

type Loaded = {
    url: string
    bbox: ImageBBox
    width: number
    height: number
    from: string
    to: string
}

function SentinelPanel({ fields }: { fields: Field[] }) {
    const request = useMemo(() => {
        const bbox = fieldsBBox(fields)
        return { bbox, ...imageSize(bbox), ...dateRange() }
    }, [fields])

    const [image, setImage] = useState<Loaded | null>(null)
    const [isLoading, setIsLoading] = useState(false)
    const [error, setError] = useState<string | null>(null)

    useEffect(() => {
        const controller = new AbortController()
        setIsLoading(true)
        setError(null)

        const timer = setTimeout(() => {
            fetchSentinelImage(request, controller.signal)
                .then((blob) => {
                    setImage({ url: URL.createObjectURL(blob), ...request })
                    setIsLoading(false)
                })
                .catch((err) => {
                    if (controller.signal.aborted) return
                    setError(
                        err instanceof Error
                            ? err.message
                            : "Failed to load image",
                    )
                    setIsLoading(false)
                })
        }, DEBOUNCE_MS)

        return () => {
            clearTimeout(timer)
            controller.abort()
        }
    }, [request])

    // free the previous blob once it's replaced or the panel closes
    useEffect(() => {
        if (!image) return
        return () => URL.revokeObjectURL(image.url)
    }, [image])

    return (
        <aside className="sentinel-panel">
            <div className="sentinel-header">
                <span>Sentinel-2</span>
                <span className="sentinel-count">
                    {fields.length} field{fields.length === 1 ? "" : "s"}
                </span>
            </div>

            {image ? (
                <div
                    className={`sentinel-image${isLoading ? " loading" : ""}`}
                    style={{ aspectRatio: `${image.width} / ${image.height}` }}
                >
                    <img src={image.url} alt="Sentinel-2 true colour" />
                    <FieldOutlines
                        fields={fields}
                        bbox={image.bbox}
                        width={image.width}
                        height={image.height}
                    />
                </div>
            ) : (
                !error && <div className="sentinel-placeholder" />
            )}

            {isLoading && <div className="sentinel-status">Loading…</div>}
            {error && <div className="sentinel-error">{error}</div>}
            {image && (
                <div className="sentinel-caption">
                    True colour, least cloudy pixels {image.from} to {image.to}
                </div>
            )}
        </aside>
    )
}

// the image is plain lng/lat (EPSG:4326), so outlines map onto it linearly
function FieldOutlines({
    fields,
    bbox,
    width,
    height,
}: {
    fields: Field[]
    bbox: ImageBBox
    width: number
    height: number
}) {
    const x = (lng: number) =>
        ((lng - bbox.west) / (bbox.east - bbox.west)) * width
    const y = (lat: number) =>
        ((bbox.north - lat) / (bbox.north - bbox.south)) * height

    return (
        <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
            {fields.map((field) => (
                <polygon
                    key={field.id}
                    points={field.geometry.coordinates[0]
                        .map(([lng, lat]) => `${x(lng)},${y(lat)}`)
                        .join(" ")}
                />
            ))}
        </svg>
    )
}

export default SentinelPanel
