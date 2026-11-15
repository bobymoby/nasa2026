import { Level } from "../../api/fetchStrategies"
import { LEVEL_LABELS } from "./labels"

const RISK_ICONS: Record<Level, string> = {
    low: "✓",
    medium: "!",
    high: "▲",
    unknown: "?",
}

// "Low", "Medium" or "High" followed by the label; pass text to replace the
// level word entirely (e.g. "No rotation risks")
export default function RiskBadge({
    level,
    label,
    text,
}: {
    level: Level
    label?: string
    text?: string
}) {
    return (
        <span className={`risk-badge risk-${level}`}>
            <span aria-hidden="true">{RISK_ICONS[level]}</span>
            {text ?? `${LEVEL_LABELS[level]} ${label ?? ""}`.trim()}
        </span>
    )
}
