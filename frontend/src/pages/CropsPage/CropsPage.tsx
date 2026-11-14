import "./CropsPage.scss"
import { useEffect, useState } from "react"
import { Link } from "react-router"

import { Crop, fetchCrops } from "../../api/fetchCrops"
import { AreaSelection, describeSelection } from "../../data/point"

type Props = {
    selection: AreaSelection
    selectedCrops: string[]
    onSelectedCropsChange: (ids: string[]) => void
}

function CropsPage({
    selection,
    selectedCrops,
    onSelectedCropsChange,
}: Props) {
    const [crops, setCrops] = useState<Crop[] | null>(null)
    const [error, setError] = useState<string | null>(null)
    const [reloadKey, setReloadKey] = useState(0)

    useEffect(() => {
        const controller = new AbortController()
        setCrops(null)
        setError(null)

        fetchCrops(selection, controller.signal)
            .then(setCrops)
            .catch((err) => {
                if (controller.signal.aborted) return
                setError(
                    err instanceof Error ? err.message : "Failed to load crops",
                )
            })

        return () => controller.abort()
    }, [selection, reloadKey])

    // drop previously picked crops that aren't available for this area
    useEffect(() => {
        if (!crops) return
        const available = new Set(crops.map((c) => c.id))
        const kept = selectedCrops.filter((id) => available.has(id))
        if (kept.length !== selectedCrops.length) onSelectedCropsChange(kept)
    }, [crops, selectedCrops, onSelectedCropsChange])

    const toggle = (id: string) => {
        onSelectedCropsChange(
            selectedCrops.includes(id)
                ? selectedCrops.filter((c) => c !== id)
                : [...selectedCrops, id],
        )
    }

    return (
        <div className="crops-page">
            <Link to="/" className="crops-back">
                ← Back to map
            </Link>
            <h1>Select crops</h1>
            <p className="crops-area">Area: {describeSelection(selection)}</p>

            {error && (
                <div className="crops-error">
                    {error}{" "}
                    <button onClick={() => setReloadKey((k) => k + 1)}>
                        Retry
                    </button>
                </div>
            )}
            {!error && !crops && <p>Loading available crops…</p>}
            {crops && crops.length === 0 && (
                <p>No crops are available for this area.</p>
            )}
            {crops && crops.length > 0 && (
                <>
                    <ul className="crops-list">
                        {crops.map((crop) => (
                            <li key={crop.id}>
                                <label>
                                    <input
                                        type="checkbox"
                                        checked={selectedCrops.includes(
                                            crop.id,
                                        )}
                                        onChange={() => toggle(crop.id)}
                                    />
                                    {crop.name}
                                </label>
                            </li>
                        ))}
                    </ul>
                    <div className="crops-footer">
                        <p className="crops-count">
                            {selectedCrops.length} of {crops.length} selected
                        </p>
                        {selectedCrops.length > 0 ? (
                            <Link to="/strategy" className="crops-next">
                                Build strategies →
                            </Link>
                        ) : (
                            <span className="crops-next disabled">
                                Pick a crop to continue
                            </span>
                        )}
                    </div>
                </>
            )}
        </div>
    )
}

export default CropsPage
