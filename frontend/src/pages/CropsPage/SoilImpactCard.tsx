import { SoilImpact } from "../../api/fetchCrops"

// each component's range; negatives only ever take away from the score
const COMPONENTS: {
    key: keyof SoilImpact
    label: string
    hint: string
    max: number
    negative?: boolean
}[] = [
    { key: "nitrogenAdded", label: "Nitrogen added", hint: "Nitrogen the crop adds to the soil (legumes)", max: 3 },
    { key: "organicMatter", label: "Organic matter", hint: "Roots and leftover plant material", max: 3 },
    { key: "soilCover", label: "Soil cover", hint: "How long living plants protect the soil from erosion", max: 3 },
    { key: "breakCrop", label: "Break crop", hint: "Interrupts disease cycles, feeds soil life", max: 2 },
    { key: "soilDisturbance", label: "Disturbance", hint: "Ploughing, digging at harvest", max: 3, negative: true },
    { key: "removalCompaction", label: "Removal / compaction", hint: "Whole-plant removal or compaction at harvest", max: 2, negative: true },
]

function formatValue(value: number): string {
    if (value === 0) return "0"
    const text = Number.isInteger(value) ? String(Math.abs(value)) : Math.abs(value).toFixed(1)
    return value < 0 ? `−${text}` : `+${text}`
}

// a crop's effect on soil: overall 0-100 score and its six parts
function SoilImpactCard({ soil }: { soil: SoilImpact }) {
    return (
        <div className="soil-impact">
            {soil.score !== null && (
                <div className="soil-score">
                    <span className="soil-score-value">{soil.score}</span>
                    <div className="soil-score-text">
                        <span>Soil health / 100</span>
                        <div className="soil-score-track" aria-hidden="true">
                            <div style={{ width: `${soil.score}%` }} />
                        </div>
                    </div>
                </div>
            )}

            <dl className="soil-components">
                {COMPONENTS.map(({ key, label, hint, max, negative }) => {
                    const value = soil[key] as number | null
                    if (value === null) return null
                    return (
                        <div key={key} title={`${hint} (${negative ? `−${max}..0` : `0..${max}`})`}>
                            <dt>{label}</dt>
                            <dd>
                                <span className={`component-track${negative ? " negative" : ""}`}>
                                    <span style={{ width: `${(Math.abs(value) / max) * 100}%` }} />
                                </span>
                                <span className={`component-value${value < 0 ? " negative" : ""}`}>
                                    {formatValue(value)}
                                </span>
                            </dd>
                        </div>
                    )
                })}
            </dl>

            {soil.note && (
                <p className="soil-note" title={soil.note}>
                    {soil.note}
                </p>
            )}
        </div>
    )
}

export default SoilImpactCard
