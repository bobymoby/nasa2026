import {
    RiskSeverity,
    RotationIssue,
    RotationIssueKind,
} from "../../api/fetchRotationRisks"
import { Planting } from "../../api/fetchStrategies"
import { humanize } from "./labels"
import RiskBadge from "./RiskBadge"

export type RiskState =
    | { status: "loading" }
    | { status: "ok"; issues: RotationIssue[] }
    | { status: "error"; message: string }

const ISSUE_LABELS: Record<RotationIssueKind, string> = {
    drought: "Drought",
    heat: "Heat stress",
    waterlogging: "Waterlogging / excess rain",
    erosion: "Erosion",
    disease_buildup: "Disease build-up",
}

const SEVERITY_ORDER: Record<RiskSeverity, number> = { medium: 1, high: 2 }

// crop ids in planting order, the sequence the risks are checked for
export function plantingSequence(plantings: Planting[]): string[] {
    return [...plantings]
        .sort(
            (a, b) =>
                a.start.year - b.start.year || a.start.month - b.start.month,
        )
        .map((p) => p.cropId)
}

function worst(issues: RotationIssue[]): RiskSeverity {
    return issues.reduce<RiskSeverity>(
        (w, i) => (SEVERITY_ORDER[i.severity] > SEVERITY_ORDER[w] ? i.severity : w),
        "medium",
    )
}

// one-line status for the strategy cards and the comparison table
export function RiskSummary({ state }: { state: RiskState | undefined }) {
    if (!state || state.status === "loading") {
        return <span className="risk-summary muted">Checking rotation risks…</span>
    }
    if (state.status === "error") {
        return <span className="risk-summary muted">Rotation risks unavailable</span>
    }
    if (state.issues.length === 0) {
        return <RiskBadge level="low" text="No rotation risks" />
    }
    const n = state.issues.length
    return (
        <RiskBadge
            level={worst(state.issues)}
            text={`${n} rotation risk${n === 1 ? "" : "s"}`}
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
                <RiskBadge level="low" text="No medium or high risks" /> This
                crop sequence doesn't make drought, heat, waterlogging, erosion
                or disease build-up noticeably worse.
            </p>
        )
    } else {
        body = (
            <div className="rating-grid rotation-risk-grid">
                {state.issues.map((issue) => (
                    <div className="rating-card rotation-risk-card" key={issue.issue}>
                        <div className="rating-head">
                            <span>{ISSUE_LABELS[issue.issue]}</span>
                            <RiskBadge level={issue.severity} label="" />
                        </div>
                        <ul className="risk-causes">
                            {issue.causes.map((cause, i) => (
                                <li key={i}>
                                    <span className="risk-crops">
                                        {cause.crops.map(humanize).join(" → ")}
                                        {issue.causes.length > 1 && (
                                            <small> · {cause.severity}</small>
                                        )}
                                    </span>
                                    <p>{cause.reason}</p>
                                    <p className="risk-mitigation">
                                        <strong>How to reduce it:</strong>{" "}
                                        {cause.mitigation}
                                    </p>
                                </li>
                            ))}
                        </ul>
                    </div>
                ))}
            </div>
        )
    }

    return (
        <>
            <p className="risk-sequence">Planting order checked: {sequence}</p>
            {body}
        </>
    )
}
