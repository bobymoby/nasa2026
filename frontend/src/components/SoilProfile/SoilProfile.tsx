import "./SoilProfile.scss"
import { useEffect, useState } from "react"

import {
    fetchSoilSelection,
    SoilSelection as SoilSelectionData,
} from "../../api/fetchSoilSelection"
import { AreaSelection } from "../../data/point"

// drawing a rectangle updates the selection on every mouse move
const DEBOUNCE_MS = 400

const PROPERTY_LABELS: Record<string, string> = {
    pH_water: "pH (water)",
    nitrogen: "Nitrogen",
    organic_carbon: "Organic C",
    clay: "Clay",
    sand: "Sand",
    silt: "Silt",
    bulk_density: "Bulk density",
}

function plural(count: number, word: string): string {
    return `${count} ${word}${count === 1 ? "" : "s"}`
}

function samplingNote({ sampling }: SoilSelectionData): string {
    const where =
        sampling.selected_field_count != null
            ? `${plural(sampling.sample_count, "point")} from ${plural(sampling.selected_field_count, "field")}`
            : `mean of ${plural(sampling.sample_count, "point")}`
    const failed = sampling.failed_sample_count
        ? `, ${sampling.failed_sample_count} unread`
        : ""
    return `${where}${failed} · modeled, not field measurements`
}

function SoilProfile({ selection }: { selection: AreaSelection }) {
    const [profile, setProfile] = useState<SoilSelectionData | null>(null)
    const [error, setError] = useState<string | null>(null)
    const [reloadKey, setReloadKey] = useState(0)

    useEffect(() => {
        const controller = new AbortController()
        setProfile(null)
        setError(null)
        const timer = setTimeout(() => {
            fetchSoilSelection(selection, controller.signal)
                .then(setProfile)
                .catch((err) => {
                    if (controller.signal.aborted) return
                    setError(err instanceof Error ? err.message : String(err))
                })
        }, DEBOUNCE_MS)
        return () => {
            clearTimeout(timer)
            controller.abort()
        }
    }, [selection, reloadKey])

    return (
        <section
            className="soil-profile"
            aria-label="Soil profile of the selection"
        >
            <header className="soil-profile-header">
                <h2>Soil profile</h2>
                <a
                    href="https://soilgrids.org"
                    target="_blank"
                    rel="noreferrer"
                >
                    ISRIC SoilGrids · 250 m
                </a>
            </header>

            {error ? (
                <div className="soil-profile-status soil-profile-error">
                    <span>{error}</span>
                    <button
                        type="button"
                        onClick={() => setReloadKey((k) => k + 1)}
                    >
                        Retry
                    </button>
                </div>
            ) : !profile ? (
                <div className="soil-profile-status">Loading soil profile…</div>
            ) : (
                <>
                    <table className="soil-profile-table">
                        <thead>
                            <tr>
                                <th scope="col">cm</th>
                                {profile.depth_intervals_cm.map((depth) => (
                                    <th scope="col" key={depth}>
                                        {depth.replace("cm", "")}
                                    </th>
                                ))}
                            </tr>
                        </thead>
                        <tbody>
                            {Object.entries(PROPERTY_LABELS).map(
                                ([key, label]) => {
                                    const property = profile.properties[key]
                                    if (!property) return null
                                    return (
                                        <tr key={key}>
                                            <th scope="row">
                                                {label}
                                                <span>{property.unit}</span>
                                            </th>
                                            {profile.depth_intervals_cm.map(
                                                (depth) => {
                                                    const value =
                                                        property
                                                            .values_by_depth[
                                                            depth
                                                        ]
                                                    return (
                                                        <td key={depth}>
                                                            {value == null
                                                                ? "—"
                                                                : value.toLocaleString(
                                                                      undefined,
                                                                      {
                                                                          maximumFractionDigits: 2,
                                                                      },
                                                                  )}
                                                        </td>
                                                    )
                                                },
                                            )}
                                        </tr>
                                    )
                                },
                            )}
                        </tbody>
                    </table>
                    <p className="soil-profile-note">{samplingNote(profile)}</p>
                </>
            )}
        </section>
    )
}

export default SoilProfile
