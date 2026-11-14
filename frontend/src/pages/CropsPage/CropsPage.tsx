import "./CropsPage.scss"
import { useEffect, useState } from "react"
import { Link } from "react-router"

import {
    Crop,
    CropGroup,
    CropsResult,
    SuitabilityClass,
    fetchCrops,
} from "../../api/fetchCrops"
import { AreaSelection } from "../../data/point"

type Props = {
    selection: AreaSelection
    selectedCrops: string[]
    onSelectedCropsChange: (ids: string[]) => void
}

function describeSelection(selection: AreaSelection): string {
    if (selection.kind === "fields") {
        const count = selection.fields.length
        return `${count} field${count === 1 ? "" : "s"} (${selection.areaHa.toFixed(2)} ha)`
    }
    const { topLeft, bottomRight } = selection.bounds
    return (
        `top-left ${topLeft.lat.toFixed(4)}, ${topLeft.lng.toFixed(4)} · ` +
        `bottom-right ${bottomRight.lat.toFixed(4)}, ${bottomRight.lng.toFixed(4)}`
    )
}

const CLASS_LABELS: Record<SuitabilityClass, string> = {
    very_high: "Very high",
    high: "High",
    good: "Good",
    medium: "Medium",
    moderate: "Moderate",
    marginal: "Marginal",
    very_marginal: "Very marginal",
    not_suitable: "Not suitable",
}

const GROUP_LABELS: Record<CropGroup, string> = {
    cereal: "Cereals",
    oilseed: "Oilseeds",
    pulse: "Pulses",
    sugar: "Sugar crops",
    root_tuber: "Roots and tubers",
    vegetable: "Vegetables",
    fodder: "Fodder",
    fibre: "Fibre",
    other: "Other",
    bioenergy: "Bioenergy",
    tree: "Tree crops",
}

const YIELD_BASIS: Record<string, string> = {
    dry_weight: "",
    oil: " oil",
    sugar: " sugar",
    lint: " lint",
}

function formatYield(crop: Crop): string | null {
    if (crop.yieldKgHa == null || crop.yieldKgHa <= 0) return null
    const basis = YIELD_BASIS[crop.yieldBasis ?? ""] ?? ""
    return `up to ${(crop.yieldKgHa / 1000).toFixed(1)} t/ha${basis}`
}

// groups in the order their best crop ranks
function byGroup(crops: Crop[]): [CropGroup, Crop[]][] {
    const groups = new Map<CropGroup, Crop[]>()
    for (const crop of crops) {
        const list = groups.get(crop.group) ?? []
        list.push(crop)
        groups.set(crop.group, list)
    }
    return Array.from(groups.entries())
}

function CropsPage({ selection, selectedCrops, onSelectedCropsChange }: Props) {
    const [result, setResult] = useState<CropsResult | null>(null)
    const [error, setError] = useState<string | null>(null)
    const [reloadKey, setReloadKey] = useState(0)

    useEffect(() => {
        const controller = new AbortController()
        setResult(null)
        setError(null)

        fetchCrops(selection, controller.signal)
            .then(setResult)
            .catch((err) => {
                if (controller.signal.aborted) return
                setError(
                    err instanceof Error ? err.message : "Failed to load crops",
                )
            })

        return () => controller.abort()
    }, [selection, reloadKey])

    const crops = result?.crops ?? null

    // drop previously picked crops that aren't suited to this area
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
            {!error && !crops && (
                <p>
                    Checking which crops suit this area… a new area takes up to
                    ~10 s.
                </p>
            )}
            {result && (
                <p className="crops-source">
                    Crops rated at least medium for rainfed farming, on average
                    over the area, are listed (FAO GAEZ v4, 1981–2010 climate,
                    high input level;{" "}
                    {result.cells === 1
                        ? "one ~9 km grid cell"
                        : `averaged over ${result.cells} ~9 km grid cells`}
                    ).
                </p>
            )}
            {crops && crops.length === 0 && (
                <p>No field crops are rated suitable for this area.</p>
            )}
            {crops && crops.length > 0 && (
                <>
                    {byGroup(crops).map(([group, list]) => (
                        <section key={group} className="crops-group">
                            <h2>{GROUP_LABELS[group]}</h2>
                            <ul className="crops-list">
                                {list.map((crop) => (
                                    <li key={crop.id}>
                                        <label>
                                            <input
                                                type="checkbox"
                                                checked={selectedCrops.includes(
                                                    crop.id,
                                                )}
                                                onChange={() => toggle(crop.id)}
                                            />
                                            <span className="crop-body">
                                                <span className="crop-head">
                                                    <span className="crop-name">
                                                        {crop.name}
                                                    </span>
                                                    <span
                                                        className={`crop-class class-${crop.class}`}
                                                    >
                                                        {
                                                            CLASS_LABELS[
                                                                crop.class
                                                            ]
                                                        }
                                                    </span>
                                                </span>
                                                <span
                                                    className="crop-bar"
                                                    title={`Suitability ${crop.suitability} / 100`}
                                                >
                                                    <span
                                                        style={{
                                                            width: `${crop.suitability}%`,
                                                        }}
                                                    />
                                                </span>
                                                <span className="crop-meta">
                                                    {crop.suitability} / 100
                                                    {formatYield(crop) &&
                                                        ` · ${formatYield(crop)}`}
                                                </span>
                                            </span>
                                        </label>
                                    </li>
                                ))}
                            </ul>
                        </section>
                    ))}
                    <p className="crops-count">
                        {selectedCrops.length} of {crops.length} selected
                    </p>
                </>
            )}
        </div>
    )
}

export default CropsPage
