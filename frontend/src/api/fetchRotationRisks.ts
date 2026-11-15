import { API_URL } from "./apiUrl"

export type RiskSeverity = "medium" | "high"

export type RotationIssueKind =
    | "drought"
    | "heat"
    | "waterlogging"
    | "erosion"
    | "disease_buildup"

export type RotationCause = {
    severity: RiskSeverity
    // the crop, or the two crops of a step, that cause it
    crops: string[]
    reason: string
    mitigation: string
}

export type RotationIssue = {
    issue: RotationIssueKind
    // worst severity among the causes
    severity: RiskSeverity
    causes: RotationCause[]
}

// crops in planting order; returns only medium and high risks
// (an empty list means none)
export async function fetchRotationRisks(
    crops: string[],
    signal?: AbortSignal,
): Promise<RotationIssue[]> {
    const res = await fetch(`${API_URL}/api/crops/rotation-risks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ crops }),
        signal,
    })
    if (!res.ok) {
        let detail = ""
        try {
            const body = await res.json()
            if (typeof body.detail === "string") detail = body.detail
        } catch {
            // fall back to the HTTP status when the body is not JSON
        }
        throw new Error(
            detail
                ? `Failed to check rotation risks: ${detail}`
                : `Failed to check rotation risks (${res.status})`,
        )
    }
    const data: { issues: RotationIssue[] } = await res.json()
    return data.issues
}
