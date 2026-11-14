import "./Map.css"
import { useRef, useEffect, useState } from "react"
import mapboxgl, {
    GeoJSONSource,
    GeoJSONSourceSpecification,
    LngLatBoundsLike,
    MapMouseEvent,
    Marker,
} from "mapbox-gl"

import "mapbox-gl/dist/mapbox-gl.css"

import LocationSearch, { SearchResult } from "../LocationSearch/LocationSearch"
import { Bounds, Point } from "../../data/point"

const INITIAL_VIEW_STATE = {
    lat: 43.429,
    lng: 27.2296,
    zoom: 10,
}

const MAPBOX_ACCESS_TOKEN =
    "pk.eyJ1IjoiYm9ieW1vYnkiLCJhIjoiY213Z2VqczgyMGowdTMwczhpd3R1MXRnNSJ9.lNQ7yevPuKvfQWJXtMZxPg"

const STYLES = {
    default: "mapbox://styles/mapbox/standard",
    satellite: "mapbox://styles/mapbox/standard-satellite",
}

const SELECTION_SOURCE_ID = "selection"

function boundsFromCorners(a: Point, b: Point): Bounds {
    return {
        topLeft: { lat: Math.max(a.lat, b.lat), lng: Math.min(a.lng, b.lng) },
        bottomRight: {
            lat: Math.min(a.lat, b.lat),
            lng: Math.max(a.lng, b.lng),
        },
    }
}

function toLngLatBounds({ topLeft, bottomRight }: Bounds): LngLatBoundsLike {
    return [
        [topLeft.lng, bottomRight.lat],
        [bottomRight.lng, topLeft.lat],
    ]
}

function selectionGeoJSON(
    bounds: Bounds | null,
): GeoJSONSourceSpecification["data"] {
    if (!bounds) return { type: "FeatureCollection", features: [] }

    const { topLeft, bottomRight } = bounds
    return {
        type: "FeatureCollection",
        features: [
            {
                type: "Feature",
                properties: {},
                geometry: {
                    type: "Polygon",
                    coordinates: [
                        [
                            [topLeft.lng, topLeft.lat],
                            [bottomRight.lng, topLeft.lat],
                            [bottomRight.lng, bottomRight.lat],
                            [topLeft.lng, bottomRight.lat],
                            [topLeft.lng, topLeft.lat],
                        ],
                    ],
                },
            },
        ],
    }
}

const COORD_FIELDS = [
    { key: "topLeftLat", label: "Top-left lat", min: -90, max: 90 },
    { key: "topLeftLng", label: "Top-left lng", min: -180, max: 180 },
    { key: "bottomRightLat", label: "Bottom-right lat", min: -90, max: 90 },
    { key: "bottomRightLng", label: "Bottom-right lng", min: -180, max: 180 },
] as const

type CoordKey = (typeof COORD_FIELDS)[number]["key"]
type CoordInputs = Record<CoordKey, string>

const EMPTY_COORD_INPUTS: CoordInputs = {
    topLeftLat: "",
    topLeftLng: "",
    bottomRightLat: "",
    bottomRightLng: "",
}

function boundsToCoordInputs({ topLeft, bottomRight }: Bounds): CoordInputs {
    return {
        topLeftLat: topLeft.lat.toFixed(6),
        topLeftLng: topLeft.lng.toFixed(6),
        bottomRightLat: bottomRight.lat.toFixed(6),
        bottomRightLng: bottomRight.lng.toFixed(6),
    }
}

function parseCoordInputs(inputs: CoordInputs): Bounds | string {
    const values = {} as Record<CoordKey, number>
    for (const { key, label, min, max } of COORD_FIELDS) {
        const raw = inputs[key].trim()
        const value = Number(raw)
        if (raw === "" || !Number.isFinite(value)) {
            return `${label} must be a number`
        }
        if (value < min || value > max) {
            return `${label} must be between ${min} and ${max}`
        }
        values[key] = value
    }

    if (values.topLeftLat <= values.bottomRightLat) {
        return "Top-left lat must be greater than bottom-right lat"
    }
    if (values.topLeftLng >= values.bottomRightLng) {
        return "Top-left lng must be less than bottom-right lng"
    }
    return {
        topLeft: { lat: values.topLeftLat, lng: values.topLeftLng },
        bottomRight: { lat: values.bottomRightLat, lng: values.bottomRightLng },
    }
}

function createCornerMarker(): Marker {
    const el = document.createElement("div")
    el.className = "corner-handle"
    return new Marker({ element: el, draggable: true, anchor: "center" })
}

function Map({
    bounds,
    onBoundsChange,
    onConfirm,
}: {
    bounds: Bounds | null
    onBoundsChange: (bounds: Bounds | null) => void
    onConfirm: () => void
}) {
    const mapRef = useRef<mapboxgl.Map>(null)
    const mapContainerRef = useRef<HTMLDivElement>(null)

    const [isSatellite, setIsSatellite] = useState(false)
    const [isSelecting, setIsSelecting] = useState(false)

    const [coordInputs, setCoordInputs] =
        useState<CoordInputs>(EMPTY_COORD_INPUTS)
    const [coordError, setCoordError] = useState<string | null>(null)
    const [showPanel, setShowPanel] = useState(true)

    // style.load handlers need the latest selection without re-subscribing
    const selectionRef = useRef<Bounds | null>(null)
    selectionRef.current = bounds
    // read via ref so the drawing effect doesn't restart mid-drag when the
    // parent passes a new callback
    const onBoundsChangeRef = useRef(onBoundsChange)
    onBoundsChangeRef.current = onBoundsChange

    const markersRef = useRef<{ topLeft: Marker; bottomRight: Marker } | null>(
        null,
    )
    // while a corner is dragged, its marker owns the position; don't snap it
    // back to the normalised bounds until the drag ends
    const isDraggingCornerRef = useRef(false)

    useEffect(() => {
        if (!mapContainerRef.current) return

        const map = new mapboxgl.Map({
            accessToken: MAPBOX_ACCESS_TOKEN,
            container: mapContainerRef.current,
            style: STYLES.default,
            // coming back from another page: reopen on the existing selection
            ...(selectionRef.current
                ? {
                      bounds: toLngLatBounds(selectionRef.current),
                      fitBoundsOptions: { padding: 40 },
                  }
                : {
                      center: [INITIAL_VIEW_STATE.lng, INITIAL_VIEW_STATE.lat],
                      zoom: INITIAL_VIEW_STATE.zoom,
                  }),
        })
        mapRef.current = map

        // setStyle wipes custom sources/layers, so re-add them on every style load
        map.on("style.load", () => {
            map.addSource(SELECTION_SOURCE_ID, {
                type: "geojson",
                data: selectionGeoJSON(selectionRef.current),
            })
            map.addLayer({
                id: "selection-fill",
                type: "fill",
                source: SELECTION_SOURCE_ID,
                paint: { "fill-color": "#3b82f6", "fill-opacity": 0.2 },
            })
            map.addLayer({
                id: "selection-outline",
                type: "line",
                source: SELECTION_SOURCE_ID,
                paint: { "line-color": "#3b82f6", "line-width": 2 },
            })
        })

        return () => {
            markersRef.current?.topLeft.remove()
            markersRef.current?.bottomRight.remove()
            markersRef.current = null
            map.remove()
            mapRef.current = null
            appliedStyleRef.current = STYLES.default
        }
    }, [])

    // the map is created with STYLES.default; only swap when the toggle
    // actually changes, since calling setStyle while the initial style is
    // still loading crashes mapbox-gl
    const appliedStyleRef = useRef<string>(STYLES.default)

    useEffect(() => {
        const map = mapRef.current
        const style = isSatellite ? STYLES.satellite : STYLES.default
        if (!map || appliedStyleRef.current === style) return

        appliedStyleRef.current = style
        map.setStyle(style)
    }, [isSatellite])

    useEffect(() => {
        const source = mapRef.current?.getSource(SELECTION_SOURCE_ID) as
            | GeoJSONSource
            | undefined
        source?.setData(selectionGeoJSON(bounds))

        // keep the inputs in sync with drawn/cleared selections
        setCoordInputs(
            bounds ? boundsToCoordInputs(bounds) : EMPTY_COORD_INPUTS,
        )
        setCoordError(null)
    }, [bounds])

    // draggable corner markers
    useEffect(() => {
        const map = mapRef.current
        if (!map) return

        if (!bounds) {
            markersRef.current?.topLeft.remove()
            markersRef.current?.bottomRight.remove()
            markersRef.current = null
            return
        }

        if (!markersRef.current) {
            const topLeft = createCornerMarker()
            const bottomRight = createCornerMarker()
            const update = () =>
                onBoundsChangeRef.current(
                    boundsFromCorners(
                        topLeft.getLngLat(),
                        bottomRight.getLngLat(),
                    ),
                )

            for (const marker of [topLeft, bottomRight]) {
                marker.on("dragstart", () => {
                    isDraggingCornerRef.current = true
                })
                marker.on("drag", update)
                marker.on("dragend", () => {
                    isDraggingCornerRef.current = false
                    update()
                })
            }

            topLeft.setLngLat(bounds.topLeft).addTo(map)
            bottomRight.setLngLat(bounds.bottomRight).addTo(map)
            markersRef.current = { topLeft, bottomRight }
        }

        // a corner dragged past the other one swaps roles, so resync both
        if (!isDraggingCornerRef.current) {
            markersRef.current.topLeft.setLngLat(bounds.topLeft)
            markersRef.current.bottomRight.setLngLat(bounds.bottomRight)
        }
    }, [bounds])

    const applyCoordInputs = (e: React.FormEvent) => {
        e.preventDefault()
        const result = parseCoordInputs(coordInputs)
        if (typeof result === "string") {
            setCoordError(result)
            return
        }
        setIsSelecting(false)
        onBoundsChange(result)
        mapRef.current?.fitBounds(toLngLatBounds(result), { padding: 40 })
    }

    const flyToResult = (result: SearchResult) => {
        const map = mapRef.current
        if (!map) return
        if (result.bbox) {
            map.fitBounds(result.bbox, { padding: 40, maxZoom: 15 })
        } else {
            map.flyTo({ center: result.center, zoom: 14 })
        }
    }

    const getMapCenter = (): [number, number] | undefined => {
        const center = mapRef.current?.getCenter()
        return center && [center.lng, center.lat]
    }

    // rectangle drawing: press, drag, release
    useEffect(() => {
        const map = mapRef.current
        if (!map || !isSelecting) return

        let start: Point | null = null
        const canvas = map.getCanvas()

        map.dragPan.disable()
        canvas.style.cursor = "crosshair"

        const onDown = (e: MapMouseEvent) => {
            start = e.lngLat
            onBoundsChangeRef.current(boundsFromCorners(start, start))
        }
        const onMove = (e: MapMouseEvent) => {
            if (!start) return
            onBoundsChangeRef.current(boundsFromCorners(start, e.lngLat))
        }
        const onUp = (e: MapMouseEvent) => {
            if (!start) return
            const newBounds = boundsFromCorners(start, e.lngLat)
            start = null
            // ignore plain clicks without a drag
            const isEmpty =
                newBounds.topLeft.lat === newBounds.bottomRight.lat ||
                newBounds.topLeft.lng === newBounds.bottomRight.lng
            onBoundsChangeRef.current(isEmpty ? null : newBounds)
            setIsSelecting(false)
        }

        map.on("mousedown", onDown)
        map.on("mousemove", onMove)
        map.on("mouseup", onUp)

        return () => {
            map.off("mousedown", onDown)
            map.off("mousemove", onMove)
            map.off("mouseup", onUp)
            map.dragPan.enable()
            canvas.style.cursor = ""
        }
    }, [isSelecting])

    return (
        <div className="map-root">
            {showPanel ? (
                <div className="sidebar">
                    <div className="sidebar-header">
                        <span>Target area</span>
                        <button
                            type="button"
                            className="panel-toggle"
                            onClick={() => setShowPanel(false)}
                        >
                            Hide
                        </button>
                    </div>
                    <LocationSearch
                        accessToken={MAPBOX_ACCESS_TOKEN}
                        proximity={getMapCenter()}
                        onSelect={flyToResult}
                    />
                    <form className="coord-form" onSubmit={applyCoordInputs}>
                        {COORD_FIELDS.map(({ key, label, min, max }) => (
                            <label key={key}>
                                {label}
                                <input
                                    type="number"
                                    step="any"
                                    min={min}
                                    max={max}
                                    value={coordInputs[key]}
                                    onChange={(e) =>
                                        setCoordInputs((inputs) => ({
                                            ...inputs,
                                            [key]: e.target.value,
                                        }))
                                    }
                                />
                            </label>
                        ))}
                        <button type="submit">Apply coordinates</button>
                        {coordError && (
                            <div className="coord-error">{coordError}</div>
                        )}
                    </form>
                </div>
            ) : (
                <button
                    type="button"
                    className="sidebar panel-toggle"
                    onClick={() => setShowPanel(true)}
                >
                    Show panel
                </button>
            )}
            <div className="map-controls">
                <button onClick={() => setIsSatellite((s) => !s)}>
                    {isSatellite ? "Map view" : "Satellite view"}
                </button>
                <button
                    className={isSelecting ? "active" : undefined}
                    onClick={() => setIsSelecting((s) => !s)}
                >
                    {isSelecting ? "Cancel selection" : "Select area"}
                </button>
                {bounds && (
                    <button onClick={() => onBoundsChange(null)}>
                        Clear selection
                    </button>
                )}
                {bounds && !isSelecting && (
                    <button className="primary" onClick={onConfirm}>
                        Select crops →
                    </button>
                )}
            </div>
            <div id="map-container" ref={mapContainerRef} />
        </div>
    )
}

export default Map
