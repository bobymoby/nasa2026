import "./SoilLegend.scss"
import { useEffect, useState } from "react"

import {
    fetchVisibleSoilClasses,
    LngLatBox,
    SoilShare,
} from "../../api/fetchSoil"

// panning fires moveend in bursts; wait for the view to settle
const DEBOUNCE_MS = 400
// at country zoom there can be 20+ groups; show the big ones first
const COLLAPSED_COUNT = 6

function formatShare(share: number): string {
    if (share < 0.01) return "<1%"
    return `${Math.round(share * 100)}%`
}

// lists the soil groups in the current view, with their share of the land
function SoilLegend({
    box,
    enabled,
}: {
    box: LngLatBox | null
    enabled: boolean
}) {
    const [classes, setClasses] = useState<SoilShare[] | null>(null)
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState<string | null>(null)
    const [expanded, setExpanded] = useState(false)

    useEffect(() => {
        if (!box || !enabled) return
        const controller = new AbortController()
        setLoading(true)

        const timer = setTimeout(() => {
            fetchVisibleSoilClasses(box, controller.signal)
                .then((result) => {
                    setClasses(result)
                    setError(null)
                })
                .catch((err) => {
                    if (controller.signal.aborted) return
                    setError(
                        err instanceof Error
                            ? err.message
                            : "Failed to load soil legend",
                    )
                })
                .finally(() => {
                    if (!controller.signal.aborted) setLoading(false)
                })
        }, DEBOUNCE_MS)

        return () => {
            clearTimeout(timer)
            controller.abort()
        }
    }, [box, enabled])

    if (!enabled) {
        return (
            <div className="soil-legend-empty">
                Raise the opacity to show the soil layer.
            </div>
        )
    }
    if (error && !classes) {
        return <div className="coord-error">{error}</div>
    }
    if (!classes) {
        return <div className="soil-legend-skeleton" aria-busy="true" />
    }
    if (classes.length === 0) {
        return (
            <div className="soil-legend-empty">
                No soil data in view (outside Bulgaria, water or built-up area).
            </div>
        )
    }

    const shown = expanded ? classes : classes.slice(0, COLLAPSED_COUNT)
    const hidden = classes.length - shown.length

    return (
        <div className={`soil-legend${loading ? " loading" : ""}`}>
            <div className="soil-legend-bar" aria-hidden="true">
                {classes.map((c) => (
                    <span
                        key={c.name}
                        title={`${c.name} · ${formatShare(c.share)}`}
                        style={{ flexGrow: c.share, backgroundColor: c.color }}
                    />
                ))}
            </div>
            <ul className="soil-legend-list">
                {shown.map((c) => (
                    <li key={c.name}>
                        <i style={{ backgroundColor: c.color }} />
                        <span className="soil-legend-name">{c.name}</span>
                        <span className="soil-legend-share">
                            {formatShare(c.share)}
                        </span>
                    </li>
                ))}
            </ul>
            {classes.length > COLLAPSED_COUNT && (
                <button
                    type="button"
                    className="soil-legend-more"
                    onClick={() => setExpanded((e) => !e)}
                >
                    {expanded ? "Show less" : `Show ${hidden} more`}
                </button>
            )}
        </div>
    )
}

export default SoilLegend
