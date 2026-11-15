import { API_URL } from "./apiUrl"
import { memo, peek } from "./memo"

export type RotationIssueKind =
    "drought" | "heat" | "waterlogging" | "erosion" | "disease_buildup"

export function peekRotationRisks(
    crops: string[],
): RotationIssueKind[] | undefined {
    return peek(`rotation-risks:${crops}`)
}

// crops in planting order; returns the possible issues, worst first
// (an empty list means none)
export function fetchRotationRisks(
    crops: string[],
    signal?: AbortSignal,
): Promise<RotationIssueKind[]> {
    return memo(
        `rotation-risks:${crops}`,
        () => loadRotationRisks(crops),
        signal,
    )
}

async function loadRotationRisks(
    crops: string[],
): Promise<RotationIssueKind[]> {
    const res = await fetch(`${API_URL}/api/crops/rotation-risks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ crops }),
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
    const data: { issues: RotationIssueKind[] } = await res.json()
    return data.issues
}
