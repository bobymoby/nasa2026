import "./SentinelPanel.css"
import { useEffect, useMemo, useState } from "react"

import { Field } from "../../api/fetchField"
import {
    fetchSentinelImage,
    ImageBBox,
    SentinelImageType,
} from "../../api/fetchSentinelImage"

// wait for the selection to settle before asking Sentinel Hub (each request
// costs quota and takes 1-3 s)
const DEBOUNCE_MS = 1000
const LOOKBACK_DAYS = 90
// longest image side in px; Sentinel-2 is 10 m/px, so small fields get upsampled
const IMAGE_MAX_SIDE = 512
// margin around the fields, as a share of the box size, and at least ~100 m
const PADDING_RATIO = 0.1
const MIN_PADDING_DEG = 0.001

// same stops and colours as EVAL_NDVI_COLOR in the backend's sentinel_hub.py
const NDVI_LEGEND = [
    [-0.2, "rgb(166 0 38)"],
    [0, "rgb(214 48 39)"],
    [0.2, "rgb(252 173 97)"],
    [0.4, "rgb(217 240 140)"],
    [0.6, "rgb(102 189 99)"],
    [0.8, "rgb(0 105 56)"],
] as const

const MODES: { type: SentinelImageType; label: string }[] = [
    { type: "truecolor", label: "True colour" },
    { type: "ndvi-color", label: "NDVI" },
]

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
    type: SentinelImageType
    bbox: ImageBBox
    width: number
    height: number
    from: string
    to: string
}

type PerType<T> = Record<SentinelImageType, T>

const TYPES = MODES.map((mode) => mode.type)

function perType<T>(value: T): PerType<T> {
    return { truecolor: value, "ndvi-color": value }
}

function SentinelPanel({ fields }: { fields: Field[] }) {
    const [type, setType] = useState<SentinelImageType>("truecolor")

    const request = useMemo(() => {
        const bbox = fieldsBBox(fields)
        return { bbox, ...imageSize(bbox), ...dateRange() }
    }, [fields])

    // both images load together so switching between them is instant
    const [images, setImages] = useState<PerType<Loaded | null>>(() =>
        perType(null),
    )
    const [loading, setLoading] = useState<PerType<boolean>>(() =>
        perType(false),
    )
    const [errors, setErrors] = useState<PerType<string | null>>(() =>
        perType(null),
    )

    useEffect(() => {
        const controller = new AbortController()
        setLoading(perType(true))
        setErrors(perType(null))

        const timer = setTimeout(() => {
            for (const t of TYPES) {
                fetchSentinelImage({ ...request, type: t }, controller.signal)
                    .then((blob) => {
                        const loaded = {
                            url: URL.createObjectURL(blob),
                            type: t,
                            ...request,
                        }
                        setImages((prev) => ({ ...prev, [t]: loaded }))
                        setLoading((prev) => ({ ...prev, [t]: false }))
                    })
                    .catch((err) => {
                        if (controller.signal.aborted) return
                        const message =
                            err instanceof Error
                                ? err.message
                                : "Failed to load image"
                        setErrors((prev) => ({ ...prev, [t]: message }))
                        setLoading((prev) => ({ ...prev, [t]: false }))
                    })
            }
        }, DEBOUNCE_MS)

        return () => {
            clearTimeout(timer)
            controller.abort()
        }
    }, [request])

    // free each blob once it's replaced or the panel closes
    const trueColorUrl = images.truecolor?.url
    const ndviUrl = images["ndvi-color"]?.url
    useEffect(() => {
        if (!trueColorUrl) return
        return () => URL.revokeObjectURL(trueColorUrl)
    }, [trueColorUrl])
    useEffect(() => {
        if (!ndviUrl) return
        return () => URL.revokeObjectURL(ndviUrl)
    }, [ndviUrl])

    const image = images[type]
    const isLoading = loading[type]
    const error = errors[type]

    return (
        <aside className="sentinel-panel">
            <div className="sentinel-header">
                <span>Sentinel-2</span>
                <span className="sentinel-count">
                    {fields.length} field{fields.length === 1 ? "" : "s"}
                </span>
            </div>

            <div className="sentinel-modes">
                {MODES.map((mode) => (
                    <button
                        key={mode.type}
                        type="button"
                        className={mode.type === type ? "active" : undefined}
                        onClick={() => setType(mode.type)}
                    >
                        {mode.label}
                    </button>
                ))}
            </div>

            {image ? (
                <div
                    className={`sentinel-image${isLoading ? " loading" : ""}`}
                    style={{ aspectRatio: `${image.width} / ${image.height}` }}
                >
                    <img
                        src={image.url}
                        alt={
                            image.type === "truecolor"
                                ? "Sentinel-2 true colour"
                                : "Sentinel-2 NDVI"
                        }
                    />
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
            {image?.type === "ndvi-color" && <NdviLegend />}
            {image && (
                <div className="sentinel-caption">
                    {image.type === "truecolor"
                        ? "True colour"
                        : "NDVI (clouds left blank)"}
                    , least cloudy pixels {image.from} to {image.to}
                </div>
            )}
        </aside>
    )
}

function NdviLegend() {
    // stops run from -0.2 to 0.8, so value + 0.2 is the share of the bar
    const gradient = NDVI_LEGEND.map(
        ([value, color]) => `${color} ${(value + 0.2) * 100}%`,
    ).join(", ")
    return (
        <div className="ndvi-legend">
            <div
                className="ndvi-legend-bar"
                style={{ background: `linear-gradient(to right, ${gradient})` }}
            />
            <div className="ndvi-legend-labels">
                <span>bare / water</span>
                <span>sparse</span>
                <span>dense crop</span>
            </div>
        </div>
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
