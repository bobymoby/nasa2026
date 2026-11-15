import { RotationIssueKind } from "../../api/fetchRotationRisks"
import { Planting } from "../../api/fetchStrategies"
import { humanize } from "./labels"
import RiskBadge from "./RiskBadge"

export type RiskState =
    | { status: "loading" }
    | { status: "ok"; issues: RotationIssueKind[] }
    | { status: "error"; message: string }

const ISSUE_LABELS: Record<RotationIssueKind, string> = {
    drought: "Drought",
    heat: "Heat stress",
    waterlogging: "Waterlogging / excess rain",
    erosion: "Erosion",
    disease_buildup: "Disease build-up",
}

// crop ids in planting order, the sequence the risks are checked for
export function plantingSequence(plantings: Planting[]): string[] {
    return plantings
        .filter((p) => p.role !== "cover")
        .sort(
            (a, b) =>
                a.start.year - b.start.year || a.start.month - b.start.month,
        )
        .map((p) => p.cropId)
}

// one-line status for the strategy cards and the comparison table
export function RiskSummary({ state }: { state: RiskState | undefined }) {
    if (!state || state.status === "loading") {
        return (
            <span className="risk-summary muted">Checking rotation risks…</span>
        )
    }
    if (state.status === "error") {
        return (
            <span className="risk-summary muted">
                Rotation risks unavailable
            </span>
        )
    }
    if (state.issues.length === 0) {
        return <RiskBadge level="low" text="No rotation risks" />
    }
    const n = state.issues.length
    return (
        <RiskBadge
            level="medium"
            text={`${n} possible rotation risk${n === 1 ? "" : "s"}`}
        />
    )
}

export function RotationRisksSection({
    state,
    plantings,
}: {
    state: RiskState | undefined
    plantings: Planting[]
}) {
    const sequence = plantingSequence(plantings).map(humanize).join(" → ")

    let body
    if (!state || state.status === "loading") {
        body = <p className="risk-note">Checking this crop sequence…</p>
    } else if (state.status === "error") {
        body = <p className="risk-note">{state.message}</p>
    } else if (state.issues.length === 0) {
        body = (
            <p className="risk-note">
                <RiskBadge level="low" text="No possible issues" /> This crop
                sequence doesn't make drought, heat, waterlogging, erosion or
                disease build-up possible.
            </p>
        )
    } else {
        body = (
            <>
                <p className="risk-note">Possible issues:</p>
                <ul className="rotation-risk-list">
                    {state.issues.map((issue) => (
                        <li key={issue}>
                            <RiskBadge
                                level="medium"
                                text={ISSUE_LABELS[issue]}
                            />
                        </li>
                    ))}
                </ul>
            </>
        )
    }

    return (
        <>
            <p className="risk-sequence">Planting order checked: {sequence}</p>
            {body}
        </>
    )
}
