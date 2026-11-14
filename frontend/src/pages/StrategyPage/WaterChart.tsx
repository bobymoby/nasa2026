import { useState } from "react"

import { Planting } from "../../api/fetchStrategies"

// round the axis up to a clean step so gridlines land on round numbers
function niceMax(value: number): number {
    const step = 200
    return Math.max(step, Math.ceil(value / step) * step)
}

function cropsInYear(plantings: Planting[], year: number): string[] {
    return plantings
        .filter((p) => p.start.year <= year && p.end.year >= year)
        .map((p) => p.cropName)
}

// water each rotation year draws, from the plantings growing in it
function WaterChart({
    byYearMm,
    plantings,
}: {
    byYearMm: number[]
    plantings: Planting[]
}) {
    const [hovered, setHovered] = useState<number | null>(null)
    const max = niceMax(Math.max(...byYearMm))
    const ticks = [0, max / 2, max]

    return (
        <div className="water-chart">
            <div className="water-plot">
                <div className="water-grid" aria-hidden="true">
                    {ticks
                        .slice()
                        .reverse()
                        .map((t) => (
                            <div key={t}>
                                <span>{t} mm</span>
                            </div>
                        ))}
                </div>
                <div className="water-bars">
                    {byYearMm.map((mm, i) => (
                        <div
                            key={i}
                            className={`water-col${hovered === i ? " hovered" : ""}`}
                            onMouseEnter={() => setHovered(i)}
                            onMouseLeave={() => setHovered(null)}
                        >
                            <div
                                className="water-bar"
                                style={{ height: `${(mm / max) * 100}%` }}
                            >
                                <span className="water-value">{mm}</span>
                            </div>
                            {hovered === i && (
                                <div className="chart-tooltip water-tooltip">
                                    <strong>Year {i + 1}</strong>
                                    <div>{mm} mm</div>
                                    <div className="chart-tooltip-muted">
                                        {cropsInYear(plantings, i + 1).join(", ")}
                                    </div>
                                </div>
                            )}
                        </div>
                    ))}
                </div>
            </div>
            <div className="water-labels">
                {byYearMm.map((_, i) => (
                    <span key={i}>Year {i + 1}</span>
                ))}
            </div>
        </div>
    )
}

export default WaterChart
