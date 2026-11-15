import "./Map.scss"
import { useRef, useEffect, useState } from "react"
import mapboxgl, {
    GeoJSONSource,
    GeoJSONSourceSpecification,
    LngLatBoundsLike,
    MapMouseEvent,
    Marker,
    Popup,
} from "mapbox-gl"

import "mapbox-gl/dist/mapbox-gl.css"

import LocationSearch, { SearchResult } from "../LocationSearch/LocationSearch"
import NdviDebug from "../NdviDebug/NdviDebug"
import SentinelPanel from "../SentinelPanel/SentinelPanel"
import { INITIAL_VIEW_STATE, MAPBOX_ACCESS_TOKEN, STYLES } from "./mapConfig"
import SoilLegend from "../SoilLegend/SoilLegend"
import SoilProfile from "../SoilProfile/SoilProfile"
import { fetchSoil, LngLatBox, SOIL_TILES_URL } from "../../api/fetchSoil"
import { AreaSelection, Bounds, Point } from "../../data/point"
import {
    Field,
    FIELD_TILES_LAYER,
    FIELD_TILES_MIN_ZOOM,
    FIELD_TILES_URL,
    fetchField,
} from "../../api/fetchField"
import { prefetchFieldNdvi } from "../../api/fetchFieldNdvi"
import { prefetchSuitability } from "../../api/fetchCrops"
import { prefetchSiteContext } from "../../api/fetchSiteContext"

const SELECTION_SOURCE_ID = "selection"
const FIELDS_SOURCE_ID = "fields"
const SELECTED_FIELDS_SOURCE_ID = "selected-fields"
const FIELDS_FILL_LAYER_ID = "fields-fill"
const ORGANIC_LAYER_ID = "fields-organic"
const SOIL_SOURCE_ID = "soil"
const SOIL_LAYER_ID = "soil"

function soilPopupContent(
    title: string,
    rows: [string, string][] = [],
): HTMLElement {
    const el = document.createElement("div")
    const heading = document.createElement("div")
    heading.className = "soil-popup-title"
    heading.textContent = title
    el.append(heading)
    for (const [name, value] of rows) {
        const row = document.createElement("div")
        row.className = "soil-popup-row"
        row.textContent = `${name} ${value}`
        el.append(row)
    }
    return el
}

// properties every field carries in the vector tiles
type FieldTileProperties = {
    field_id?: string
    crop_category?: string
    organic?: boolean
    field_size?: number
}

// @types/geojson isn't installed, so mapbox's feature type has no `properties`
function tileProperties(e: MapMouseEvent): FieldTileProperties | undefined {
    const feature = e.features?.[0] as
        { properties?: FieldTileProperties } | undefined
    return feature?.properties
}

// selects a field the moment it is clicked; the full outline replaces it once
// GET /fields/{id} answers (the tile geometry is clipped at tile edges)
function fieldFromTile(e: MapMouseEvent): Field | undefined {
    const feature = e.features?.[0] as
        | {
              properties?: FieldTileProperties
              geometry?: { type: string; coordinates: unknown }
          }
        | undefined
    const props = feature?.properties
    const geometry = feature?.geometry
    if (typeof props?.field_id !== "string" || !geometry) return undefined
    let rings: number[][][]
    if (geometry.type === "Polygon")
        rings = geometry.coordinates as number[][][]
    else if (geometry.type === "MultiPolygon")
        rings = (geometry.coordinates as number[][][][])[0]
    else return undefined
    return {
        type: "Feature",
        id: props.field_id,
        geometry: { type: "Polygon", coordinates: rings },
        properties: {
            field_id: props.field_id,
            farm_id: null,
            crop_code: null,
            crop_name: null,
            crop_name_en: null,
            crop_category: props.crop_category ?? null,
            crop_category_code: null,
            organic: props.organic == null ? null : Number(props.organic),
            field_size: props.field_size ?? null,
            area_ha: props.field_size ?? 0,
        },
    }
}

// "winter_common_soft_wheat" -> "Winter common soft wheat"
function humanizeCategory(category: string): string {
    const words = category.replace(/_/g, " ")
    return words.charAt(0).toUpperCase() + words.slice(1)
}

function fieldTooltip(props: FieldTileProperties): HTMLElement {
    const el = document.createElement("div")
    const name = document.createElement("div")
    name.className = "field-tooltip-name"
    name.textContent = props.crop_category
        ? humanizeCategory(props.crop_category)
        : "Unknown crop"
    el.append(name)

    const details = document.createElement("div")
    details.className = "field-tooltip-details"
    details.textContent = [
        props.field_size != null && `${props.field_size.toFixed(2)} ha`,
        props.organic && "organic",
    ]
        .filter(Boolean)
        .join(" · ")
    if (details.textContent) el.append(details)
    return el
}

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
    selection,
}: {
    bounds: Bounds | null
    onBoundsChange: (bounds: Bounds | null) => void
    selectedFields: Field[]
    onSelectedFieldsChange: (fields: Field[]) => void
    onConfirm: () => void
    selection: AreaSelection | null
}) {
    const mapRef = useRef<mapboxgl.Map>(null)
    const mapContainerRef = useRef<HTMLDivElement>(null)

    const [isSatellite, setIsSatellite] = useState(false)
    const [isSelecting, setIsSelecting] = useState(false)

    const [showPanel, setShowPanel] = useState(true)
    const [zoom, setZoom] = useState(INITIAL_VIEW_STATE.zoom)
    // what's on screen, for the soil legend
    const [viewBox, setViewBox] = useState<LngLatBox | null>(null)
    const [fieldError, setFieldError] = useState<string | null>(null)
    const [showOrganic, setShowOrganic] = useState(true)
    const showOrganicRef = useRef(showOrganic)
    showOrganicRef.current = showOrganic
    // the soil layer is always on; opacity 0 hides it and stops tile requests
    const [soilOpacity, setSoilOpacity] = useState(0.4)
    const soilOpacityRef = useRef(soilOpacity)
    soilOpacityRef.current = soilOpacity
    const [showNdvi, setShowNdvi] = useState(false)

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
        const updateViewBox = () => {
            const b = map.getBounds()
            if (b) {
                setViewBox([
                    b.getWest(),
                    b.getSouth(),
                    b.getEast(),
                    b.getNorth(),
                ])
            }
        }
        map.on("load", updateViewBox)
        map.on("moveend", updateViewBox)

        // right mouse button inspects soil instead of rotating the map
        map.dragRotate.disable()
        map.touchZoomRotate.disableRotation()

        const soilPopup = new Popup({
            className: "soil-popup",
            maxWidth: "240px",
        })
        let soilController: AbortController | null = null
        map.on("contextmenu", (e) => {
            e.preventDefault()
            e.originalEvent.preventDefault()
            soilController?.abort()
            const controller = new AbortController()
            soilController = controller

            soilPopup
                .setLngLat(e.lngLat)
                .setDOMContent(soilPopupContent("Loading soil…"))
                .addTo(map)

            fetchSoil(e.lngLat.lat, e.lngLat.lng, controller.signal)
                .then((result) =>
                    soilPopup.setDOMContent(
                        result.soil
                            ? soilPopupContent(
                                  result.soil,
                                  result.probabilities.map(
                                      ({ soil, probability }) => [
                                          soil,
                                          `${probability}%`,
                                      ],
                                  ),
                              )
                            : soilPopupContent("No soil data here"),
                    ),
                )
                .catch((err) => {
                    if (controller.signal.aborted) return
                    soilPopup.setDOMContent(
                        soilPopupContent(
                            err instanceof Error
                                ? err.message
                                : "Failed to load soil",
                        ),
                    )
                })
        })

        // setStyle wipes custom sources/layers, so re-add them on every style load
        map.on("style.load", () => {
            map.addSource(SOIL_SOURCE_ID, {
                type: "raster",
                tiles: [SOIL_TILES_URL],
                tileSize: 256,
                attribution:
                    '<a href="https://soilgrids.org">SoilGrids © ISRIC</a>',
            })
            map.addLayer({
                id: SOIL_LAYER_ID,
                type: "raster",
                source: SOIL_SOURCE_ID,
                // under the Standard style's labels and roads, and under the
                // field layers, which have no slot and so draw on top
                slot: "middle",
                layout: {
                    visibility: soilOpacityRef.current > 0 ? "visible" : "none",
                },
                paint: { "raster-opacity": soilOpacityRef.current },
            })

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

            map.addLayer({
                id: ORGANIC_LAYER_ID,
                type: "line",
                source: FIELDS_SOURCE_ID,
                "source-layer": FIELD_TILES_LAYER,
                filter: ["==", ["get", "organic"], true],
                layout: {
                    visibility: showOrganicRef.current ? "visible" : "none",
                },
                paint: {
                    "line-color": "#16a34a",
                    "line-width": [
                        "interpolate",
                        ["linear"],
                        ["zoom"],
                        10,
                        1,
                        15,
                        3,
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
            soilController?.abort()
            soilPopup.remove()
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
            GeoJSONSource | undefined
        source?.setData(selectionGeoJSON(bounds))
    }, [bounds])

    useEffect(() => {
        const map = mapRef.current
        if (!map?.getLayer(ORGANIC_LAYER_ID)) return
        map.setLayoutProperty(
            ORGANIC_LAYER_ID,
            "visibility",
            showOrganic ? "visible" : "none",
        )
    }, [showOrganic])

    useEffect(() => {
        const map = mapRef.current
        if (!map?.getLayer(SOIL_LAYER_ID)) return
        map.setLayoutProperty(
            SOIL_LAYER_ID,
            "visibility",
            soilOpacity > 0 ? "visible" : "none",
        )
        map.setPaintProperty(SOIL_LAYER_ID, "raster-opacity", soilOpacity)
    }, [soilOpacity])

    useEffect(() => {
        const source = mapRef.current?.getSource(SELECTED_FIELDS_SOURCE_ID) as
            GeoJSONSource | undefined
        source?.setData(selectedFieldsGeoJSON(selectedFields))
    }, [selectedFields])

    // click a field to toggle it in the selection; disabled while drawing
    // the rectangle so the two don't fight over mouse events
    useEffect(() => {
        const map = mapRef.current
        if (!map || isSelecting) return

        const canvas = map.getCanvas()
        const tooltip = new Popup({
            closeButton: false,
            closeOnClick: false,
            className: "field-tooltip",
            offset: 12,
            maxWidth: "260px",
        })
        let hoveredFieldId: string | undefined

        const onMove = (e: MapMouseEvent) => {
            canvas.style.cursor = "pointer"
            const props = tileProperties(e)
            if (!props) return
            // only rebuild the content when moving onto another field
            if (props.field_id !== hoveredFieldId) {
                hoveredFieldId = props.field_id
                tooltip.setDOMContent(fieldTooltip(props))
            }
            tooltip.setLngLat(e.lngLat)
            if (!tooltip.isOpen()) tooltip.addTo(map)
        }
        const onLeave = () => {
            canvas.style.cursor = ""
            hoveredFieldId = undefined
            tooltip.remove()
        }
        const onClick = (e: MapMouseEvent) => {
            const provisional = fieldFromTile(e)
            if (!provisional) return
            const fieldId = provisional.id

            const current = selectedFieldsRef.current
            if (current.some((f) => f.id === fieldId)) {
                onSelectedFieldsChangeRef.current(
                    current.filter((f) => f.id !== fieldId),
                )
                return
            }

            onSelectedFieldsChangeRef.current([...current, provisional])
            prefetchSuitability([provisional])

            const pending = pendingFieldsRef.current
            if (pending.has(fieldId)) return
            pending.add(fieldId)
            setFieldError(null)
            fetchField(fieldId)
                .then((field) => {
                    const latest = selectedFieldsRef.current
                    if (!latest.some((f) => f.id === field.id)) return
                    onSelectedFieldsChangeRef.current(
                        latest.map((f) => (f.id === field.id ? field : f)),
                    )
                    prefetchFieldNdvi([field])
                    prefetchSiteContext([field])
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

        map.on("mousemove", FIELDS_FILL_LAYER_ID, onMove)
        map.on("mouseleave", FIELDS_FILL_LAYER_ID, onLeave)
        map.on("click", FIELDS_FILL_LAYER_ID, onClick)

        return () => {
            map.off("mousemove", FIELDS_FILL_LAYER_ID, onMove)
            map.off("mouseleave", FIELDS_FILL_LAYER_ID, onLeave)
            map.off("click", FIELDS_FILL_LAYER_ID, onClick)
            tooltip.remove()
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
            // right button is for soil inspection
            if (e.originalEvent.button !== 0) return
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
                    <div className="fields-panel">
                        <div className="sidebar-header">
                            <span>Fields ({selectedFields.length})</span>
                            {selectedFields.length > 0 && (
                                <span className="fields-actions">
                                    <button
                                        type="button"
                                        className={`panel-toggle${showNdvi ? " active" : ""}`}
                                        title="Send the selected field rings to /api/fields/ndvi"
                                        onClick={() => setShowNdvi((s) => !s)}
                                    >
                                        NDVI debug
                                    </button>
                                    <button
                                        type="button"
                                        className="panel-toggle"
                                        onClick={() =>
                                            onSelectedFieldsChange([])
                                        }
                                    >
                                        Clear
                                    </button>
                                </span>
                            )}
                        </div>
                        <label className="organic-toggle">
                            <input
                                type="checkbox"
                                checked={showOrganic}
                                onChange={(e) =>
                                    setShowOrganic(e.target.checked)
                                }
                            />
                            <span className="organic-swatch" />
                            Highlight organic fields
                        </label>
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
                                                    {
                                                        padding: 80,
                                                        maxZoom: 16,
                                                    },
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
                                                        (f) =>
                                                            f.id !== field.id,
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
                    <div className="soil-panel">
                        <div className="sidebar-header">
                            <span>Soil types</span>
                        </div>
                        <div className="fields-hint">
                            Most probable WRB soil group, 250 m cells (ISRIC
                            SoilGrids). Right-click the map for details.
                        </div>
                        <label className="soil-opacity">
                            Opacity {Math.round(soilOpacity * 100)}%
                            <input
                                type="range"
                                min={0}
                                max={1}
                                step={0.05}
                                value={soilOpacity}
                                onChange={(e) =>
                                    setSoilOpacity(Number(e.target.value))
                                }
                            />
                        </label>
                        <SoilLegend box={viewBox} enabled={soilOpacity > 0} />
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
                {(bounds || selectedFields.length > 0) && !isSelecting && (
                    <button className="primary" onClick={onConfirm}>
                        Select crops →
                    </button>
                )}
            </div>
            {selectedFields.length > 0 && (
                <SentinelPanel fields={selectedFields} />
            )}
            {selection && <SoilProfile selection={selection} />}
            {showNdvi && selectedFields.length > 0 && (
                <NdviDebug
                    fields={selectedFields}
                    fieldLabel={fieldLabel}
                    onClose={() => setShowNdvi(false)}
                />
            )}
            <div id="map-container" ref={mapContainerRef} />
        </div>
    )
}

export default Map
