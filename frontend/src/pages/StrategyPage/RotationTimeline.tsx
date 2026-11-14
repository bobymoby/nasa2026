import { useState } from "react"

import { Planting, PlantingRole } from "../../api/fetchStrategies"
import { formatMonth, MONTHS, ROLE_LABELS, TRAIT_LABELS } from "./labels"

type Segment = {
    planting: Planting
    year: number
    // inclusive calendar months within this year
    from: number
    to: number
    continuesBefore: boolean
    continuesAfter: boolean
}

// a planting that spans winter is drawn as one piece per calendar year
function segments(plantings: Planting[]): Segment[] {
    return plantings.flatMap((planting) => {
        const out: Segment[] = []
        for (let y = planting.start.year; y <= planting.end.year; y++) {
            const continuesBefore = y > planting.start.year
            const continuesAfter = y < planting.end.year
            out.push({
                planting,
                year: y,
                from: continuesBefore ? 1 : planting.start.month,
                to: continuesAfter ? 12 : planting.end.month,
                continuesBefore,
                continuesAfter,
            })
        }
        return out
    })
}

const ROLES: PlantingRole[] = ["cash", "forage", "cover"]

type Hover = { planting: Planting; x: number; y: number }

// one row per rotation year, January to December, so "what and when" reads
// straight across
function RotationTimeline({
    plantings,
    years,
}: {
    plantings: Planting[]
    years: number
}) {
    const [hover, setHover] = useState<Hover | null>(null)
    const all = segments(plantings)
    const roles = ROLES.filter((r) => plantings.some((p) => p.role === r))

    const track = (e: React.MouseEvent, planting: Planting) => {
        const box = e.currentTarget
            .closest(".rotation")
            ?.getBoundingClientRect()
        if (!box) return
        setHover({ planting, x: e.clientX - box.left, y: e.clientY - box.top })
    }

    return (
        <div className="rotation" onMouseLeave={() => setHover(null)}>
            <div className="rotation-legend">
                {roles.map((r) => (
                    <span key={r}>
                        <i className={`role-${r}`} />
                        {ROLE_LABELS[r]}
                    </span>
                ))}
            </div>

            <div className="rotation-grid">
                <div className="rotation-months">
                    <span />
                    <div>
                        {MONTHS.map((m) => (
                            <span key={m}>{m}</span>
                        ))}
                    </div>
                </div>

                {Array.from({ length: years }, (_, i) => i + 1).map((year) => (
                    <div className="rotation-row" key={year}>
                        <span className="rotation-year">Year {year}</span>
                        <div className="rotation-track">
                            {all
                                .filter((s) => s.year === year)
                                .map((s) => {
                                    const months = s.to - s.from + 1
                                    const isHovered =
                                        hover?.planting === s.planting
                                    return (
                                        <div
                                            key={`${s.planting.cropId}-${s.planting.start.year}-${s.planting.start.month}`}
                                            className={[
                                                "rotation-bar",
                                                `role-${s.planting.role}`,
                                                s.continuesBefore && "cont-before",
                                                s.continuesAfter && "cont-after",
                                                isHovered && "hovered",
                                                hover && !isHovered && "dimmed",
                                            ]
                                                .filter(Boolean)
                                                .join(" ")}
                                            style={{
                                                left: `${((s.from - 1) / 12) * 100}%`,
                                                width: `${(months / 12) * 100}%`,
                                            }}
                                            onMouseMove={(e) =>
                                                track(e, s.planting)
                                            }
                                        >
                                            {months >= 2 && (
                                                <span>{s.planting.cropName}</span>
                                            )}
                                        </div>
                                    )
                                })}
                        </div>
                    </div>
                ))}
            </div>

            {hover && (
                <div
                    className="chart-tooltip"
                    style={{ left: hover.x, top: hover.y }}
                    role="tooltip"
                >
                    <strong>{hover.planting.cropName}</strong>
                    <div className="chart-tooltip-muted">
                        {ROLE_LABELS[hover.planting.role]}
                    </div>
                    <div>
                        {formatMonth(hover.planting.start)} →{" "}
                        {formatMonth(hover.planting.end)}
                    </div>
                    <div>{hover.planting.waterUseMm} mm water</div>
                    {hover.planting.traits.length > 0 && (
                        <div className="chart-tooltip-muted">
                            {hover.planting.traits
                                .map((t) => TRAIT_LABELS[t])
                                .join(" · ")}
                        </div>
                    )}
                    {hover.planting.note && (
                        <div className="chart-tooltip-note">
                            {hover.planting.note}
                        </div>
                    )}
                </div>
            )}
        </div>
    )
}

export default RotationTimeline
