import { useEffect, useState } from "react"

import {
    fetchSoilSelection,
    SoilSelection as SoilSelectionData,
} from "../../api/fetchSoilSelection"
import { AreaSelection } from "../../data/point"

const PROPERTY_LABELS: Record<string, string> = {
    pH_water: "pH (water)",
    nitrogen: "Nitrogen",
    organic_carbon: "Organic carbon",
    clay: "Clay",
    sand: "Sand",
    silt: "Silt",
    bulk_density: "Bulk density",
}

function SoilProfile({
    selection,
    reloadKey,
    onRetry,
}: {
    selection: AreaSelection
    reloadKey: number
    onRetry: () => void
}) {
    const [profile, setProfile] = useState<SoilSelectionData | null>(null)
    const [error, setError] = useState<string | null>(null)
    const [loading, setLoading] = useState(true)

    useEffect(() => {
        const controller = new AbortController()
        setProfile(null)
        setError(null)
        setLoading(true)
        fetchSoilSelection(selection, controller.signal)
            .then(setProfile)
            .catch((err) => {
                if (controller.signal.aborted) return
                setError(err instanceof Error ? err.message : String(err))
            })
            .finally(() => {
                if (!controller.signal.aborted) setLoading(false)
            })
        return () => controller.abort()
    }, [selection, reloadKey])

    return (
        <section className="soil-profile" aria-label="Selected-area soil profile">
            <header className="soil-profile-header">
                <h2>Soil profile</h2>
                <span>
                    <a href="https://soilgrids.org" target="_blank" rel="noreferrer">
                        ISRIC SoilGrids
                    </a>
                    {" · "}
                    modeled estimates at approximately 250 m resolution
                </span>
            </header>

            {loading && <p className="soil-profile-status">Loading soil profile…</p>}
            {error && (
                <div className="soil-profile-error">
                    <span>{error}</span>
                    <button type="button" onClick={onRetry}>
                        Retry
                    </button>
                </div>
            )}
            {profile && (
                <>
                    <div className="soil-profile-table-wrap">
                        <table className="soil-profile-table">
                            <thead>
                                <tr>
                                    <th scope="col">Property</th>
                                    {profile.depth_intervals_cm.map((depth) => (
                                        <th scope="col" key={depth}>
                                            {depth}
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
                                                    <span>
                                                        {property.unit}
                                                    </span>
                                                </th>
                                                {profile.depth_intervals_cm.map(
                                                    (depth) => {
                                                        const value =
                                                            property.values_by_depth[
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
                    </div>
                    <p className="soil-profile-note">
                        {profile.sampling.selected_field_count != null
                            ? `Sampled at ${profile.sampling.sample_count} representative point${profile.sampling.sample_count === 1 ? "" : "s"} chosen from ${profile.sampling.selected_field_count} selected field${profile.sampling.selected_field_count === 1 ? "" : "s"}.`
                            : `Averaged from ${profile.sampling.sample_count} sample point${profile.sampling.sample_count === 1 ? "" : "s"} in the selected rectangle.`}{" "}
                        Values are area context, not crop-specific scores or
                        field measurements.
                    </p>
                </>
            )}
        </section>
    )
}

export default SoilProfile
