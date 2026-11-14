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
import {
    Field,
    FIELD_TILES_LAYER,
    FIELD_TILES_MIN_ZOOM,
    FIELD_TILES_URL,
    fetchField,
} from "../../api/fetchField"

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
const FIELDS_SOURCE_ID = "fields"
const SELECTED_FIELDS_SOURCE_ID = "selected-fields"
const FIELDS_FILL_LAYER_ID = "fields-fill"

function selectedFieldsGeoJSON(
    fields: Field[],
): GeoJSONSourceSpecification["data"] {
    return { type: "FeatureCollection", features: fields }
}

function fieldLabel({ properties }: Field): string {
    return (
        properties.crop_name_en ??
        properties.crop_name ??
        `Field ${properties.field_id}`
    )
}

function fieldBounds({ geometry }: Field): LngLatBoundsLike {
    const [ring] = geometry.coordinates
    const lngs = ring.map(([lng]) => lng)
    const lats = ring.map(([, lat]) => lat)
    return [
        [Math.min(...lngs), Math.min(...lats)],
        [Math.max(...lngs), Math.max(...lats)],
    ]
}

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
    selectedFields,
    onSelectedFieldsChange,
    onConfirm,
}: {
    bounds: Bounds | null
    onBoundsChange: (bounds: Bounds | null) => void
    selectedFields: Field[]
    onSelectedFieldsChange: (fields: Field[]) => void
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
    const [zoom, setZoom] = useState(INITIAL_VIEW_STATE.zoom)
    const [fieldError, setFieldError] = useState<string | null>(null)

    // style.load handlers need the latest selection without re-subscribing
    const selectionRef = useRef<Bounds | null>(null)
    selectionRef.current = bounds
    // read via ref so the drawing effect doesn't restart mid-drag when the
    // parent passes a new callback
    const onBoundsChangeRef = useRef(onBoundsChange)
    onBoundsChangeRef.current = onBoundsChange
    // same for the field click handler, which is registered once per mode
    const selectedFieldsRef = useRef(selectedFields)
    selectedFieldsRef.current = selectedFields
    const onSelectedFieldsChangeRef = useRef(onSelectedFieldsChange)
    onSelectedFieldsChangeRef.current = onSelectedFieldsChange
    // field ids being fetched, so a double click doesn't add a field twice
    const pendingFieldsRef = useRef(new Set<string>())

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
        setZoom(map.getZoom())
        map.on("zoomend", () => setZoom(map.getZoom()))

        // setStyle wipes custom sources/layers, so re-add them on every style load
        map.on("style.load", () => {
            map.addSource(FIELDS_SOURCE_ID, {
                type: "vector",
                tiles: [FIELD_TILES_URL],
                minzoom: FIELD_TILES_MIN_ZOOM,
                maxzoom: 14,
            })
            map.addLayer({
                id: FIELDS_FILL_LAYER_ID,
                type: "fill",
                source: FIELDS_SOURCE_ID,
                "source-layer": FIELD_TILES_LAYER,
                paint: { "fill-color": "#facc15", "fill-opacity": 0.15 },
            })
            map.addLayer({
                id: "fields-outline",
                type: "line",
                source: FIELDS_SOURCE_ID,
                "source-layer": FIELD_TILES_LAYER,
                paint: {
                    "line-color": "#ca8a04",
                    "line-width": [
                        "interpolate",
                        ["linear"],
                        ["zoom"],
                        10,
                        0.3,
                        15,
                        1.2,
                    ],
                },
            })

            // selected fields come from their full GeoJSON, so they stay
            // visible below the tiles' min zoom
            map.addSource(SELECTED_FIELDS_SOURCE_ID, {
                type: "geojson",
                data: selectedFieldsGeoJSON(selectedFieldsRef.current),
            })
            map.addLayer({
                id: "selected-fields-fill",
                type: "fill",
                source: SELECTED_FIELDS_SOURCE_ID,
                paint: { "fill-color": "#f97316", "fill-opacity": 0.5 },
            })
            map.addLayer({
                id: "selected-fields-outline",
                type: "line",
                source: SELECTED_FIELDS_SOURCE_ID,
                paint: { "line-color": "#c2410c", "line-width": 2 },
            })

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

    useEffect(() => {
        const source = mapRef.current?.getSource(SELECTED_FIELDS_SOURCE_ID) as
            | GeoJSONSource
            | undefined
        source?.setData(selectedFieldsGeoJSON(selectedFields))
    }, [selectedFields])

    // click a field to toggle it in the selection; disabled while drawing
    // the rectangle so the two don't fight over mouse events
    useEffect(() => {
        const map = mapRef.current
        if (!map || isSelecting) return

        const canvas = map.getCanvas()
        const onEnter = () => {
            canvas.style.cursor = "pointer"
        }
        const onLeave = () => {
            canvas.style.cursor = ""
        }
        const onClick = (e: MapMouseEvent) => {
            // @types/geojson isn't installed, so mapbox's feature type has no
            // `properties`; tiles always carry field_id as a string
            const feature = e.features?.[0] as
                | { properties?: { field_id?: unknown } }
                | undefined
            const fieldId = feature?.properties?.field_id
            if (typeof fieldId !== "string") return

            const current = selectedFieldsRef.current
            if (current.some((f) => f.id === fieldId)) {
                onSelectedFieldsChangeRef.current(
                    current.filter((f) => f.id !== fieldId),
                )
                return
            }

            const pending = pendingFieldsRef.current
            if (pending.has(fieldId)) return
            pending.add(fieldId)
            setFieldError(null)
            fetchField(fieldId)
                .then((field) => {
                    const latest = selectedFieldsRef.current
                    if (latest.some((f) => f.id === field.id)) return
                    onSelectedFieldsChangeRef.current([...latest, field])
                })
                .catch((err) =>
                    setFieldError(
                        err instanceof Error
                            ? err.message
                            : "Failed to load field",
                    ),
                )
                .finally(() => pending.delete(fieldId))
        }

        map.on("mouseenter", FIELDS_FILL_LAYER_ID, onEnter)
        map.on("mouseleave", FIELDS_FILL_LAYER_ID, onLeave)
        map.on("click", FIELDS_FILL_LAYER_ID, onClick)

        return () => {
            map.off("mouseenter", FIELDS_FILL_LAYER_ID, onEnter)
            map.off("mouseleave", FIELDS_FILL_LAYER_ID, onLeave)
            map.off("click", FIELDS_FILL_LAYER_ID, onClick)
            canvas.style.cursor = ""
        }
    }, [isSelecting])

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
                    <div className="fields-panel">
                        <div className="sidebar-header">
                            <span>Fields ({selectedFields.length})</span>
                            {selectedFields.length > 0 && (
                                <button
                                    type="button"
                                    className="panel-toggle"
                                    onClick={() => onSelectedFieldsChange([])}
                                >
                                    Clear
                                </button>
                            )}
                        </div>
                        {zoom < FIELD_TILES_MIN_ZOOM ? (
                            <div className="fields-hint">
                                Zoom in to see fields
                            </div>
                        ) : (
                            selectedFields.length === 0 && (
                                <div className="fields-hint">
                                    Click a field to select it
                                </div>
                            )
                        )}
                        {fieldError && (
                            <div className="coord-error">{fieldError}</div>
                        )}
                        {selectedFields.length > 0 && (
                            <ul className="fields-list">
                                {selectedFields.map((field) => (
                                    <li key={field.id}>
                                        <button
                                            type="button"
                                            className="field-name"
                                            title={fieldLabel(field)}
                                            onClick={() =>
                                                mapRef.current?.fitBounds(
                                                    fieldBounds(field),
                                                    { padding: 80, maxZoom: 16 },
                                                )
                                            }
                                        >
                                            {fieldLabel(field)}
                                        </button>
                                        <span className="field-area">
                                            {field.properties.area_ha.toFixed(
                                                2,
                                            )}{" "}
                                            ha
                                        </span>
                                        <button
                                            type="button"
                                            className="field-remove"
                                            aria-label="Remove field"
                                            onClick={() =>
                                                onSelectedFieldsChange(
                                                    selectedFields.filter(
                                                        (f) => f.id !== field.id,
                                                    ),
                                                )
                                            }
                                        >
                                            ×
                                        </button>
                                    </li>
                                ))}
                            </ul>
                        )}
                    </div>
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
