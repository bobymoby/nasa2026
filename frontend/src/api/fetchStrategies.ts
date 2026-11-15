import { AreaSelection } from "../data/point"
import { API_URL } from "./apiUrl"

// the backend gives up on slow site sources after 20 s; this covers a hung server
const CONTEXT_TIMEOUT_MS = 30_000

function areaBody(selection: AreaSelection) {
    return selection.kind === "bounds"
        ? { bounds: selection.bounds }
        : { fields: selection.fields }
}

// crops with complete planning data (agronomy, prices, yields, costs) for the area
export async function fetchPlannableCrops(
    selection: AreaSelection,
    signal?: AbortSignal,
): Promise<string[]> {
    const res = await fetch(`${API_URL}/api/strategies/crops`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(areaBody(selection)),
        signal,
    })
    if (!res.ok)
        throw new Error(`Failed to load plannable crops (${res.status})`)
    const data: { crops: string[] } = await res.json()
    return data.crops
}

export type Level = "low" | "medium" | "high" | "unknown"
export type PlantingRole = "cash" | "cover" | "forage"
export type Trait =
    "nitrogen_fixing" | "deep_rooting" | "drought_tolerant" | "cover"

// rotation year (1-based) and calendar month (1-12)
export type MonthRef = { year: number; month: number }

export type Planting = {
    cropId: string
    cropName: string
    role: PlantingRole
    start: MonthRef
    end: MonthRef
    // total over the whole planting
    waterUseMm: number
    traits: Trait[]
    note: string
}

export type Rating = { score: number; summary: string }
export type Assessment = { level: Level; summary: string }

// gross revenue in EUR; revenuePerHa is one pass through the rotation
export type Economics = {
    currency: "EUR"
    // ISO alpha-2 country whose market figures are used
    country: string
    // prices are real euros of this year
    priceYear: number
    areaHa: number
    revenuePerHa: number
    revenuePerHaYear: number
    // revenuePerHa over the whole rotation, scaled to the selected area
    totalRevenue: number
    plantings: {
        cropName: string
        role: PlantingRole
        yieldTHa: number
        priceEurT: number
        revenue: number
        // "price eurostat 2020–2024, yield eurostat 2021–2025"
        source: string
    }[]
    byYear: { year: number; revenue: number }[]
}

export type Strategy = {
    id: string
    name: string
    summary: string
    durationYears: number
    // the requested crop ids this plan grows
    matchedCrops: string[]
    rotation: string[]
    plantings: Planting[]
    ratings: {
        nutrientCycling: Rating
        organicMatter: Rating
        pestDisruption: Rating
        foragePotential: Rating
        market: Rating
        soilImprovement: Rating
    }
    erosionRisk: Assessment
    waterUse: Assessment & { byYearMm: number[] }
    cropRoles: {
        nitrogenFixing: Assessment
        coverCrops: Assessment
        deepRooting: Assessment
        droughtTolerant: Assessment
    }
    marketConsiderations: string[]
    economics: Economics
}

export type StrategiesResult = {
    assumptions: string[]
    strategies: Strategy[]
}

export async function fetchStrategies(
    selection: AreaSelection,
    crops: string[],
    signal?: AbortSignal,
): Promise<StrategiesResult> {
    const area = areaBody(selection)
    const contextArea =
        selection.kind === "bounds"
            ? {
                  plots: [
                      [
                          [
                              selection.bounds.topLeft.lat,
                              selection.bounds.topLeft.lng,
                          ],
                          [
                              selection.bounds.topLeft.lat,
                              selection.bounds.bottomRight.lng,
                          ],
                          [
                              selection.bounds.bottomRight.lat,
                              selection.bounds.bottomRight.lng,
                          ],
                          [
                              selection.bounds.bottomRight.lat,
                              selection.bounds.topLeft.lng,
                          ],
                      ],
                  ],
              }
            : { fields: selection.fields }

    let siteContext: unknown
    const context = new AbortController()
    const abortContext = () => context.abort()
    signal?.addEventListener("abort", abortContext)
    const timer = setTimeout(abortContext, CONTEXT_TIMEOUT_MS)
    try {
        const contextResponse = await fetch(
            `${API_URL}/api/fields/strategy-context`,
            {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(contextArea),
                signal: context.signal,
            },
        )
        if (!contextResponse.ok) {
            throw new Error(
                `Failed to load field data (${contextResponse.status})`,
            )
        }
        siteContext = await contextResponse.json()
    } catch (error) {
        if (signal?.aborted || !context.signal.aborted) throw error
        throw new Error(
            `Field data took longer than ${CONTEXT_TIMEOUT_MS / 1000} s to load; try again.`,
        )
    } finally {
        clearTimeout(timer)
        signal?.removeEventListener("abort", abortContext)
    }

    const res = await fetch(`${API_URL}/api/strategies`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            crops,
            ...area,
            siteContext,
        }),
        signal,
    })
    if (!res.ok) {
        let detail = ""
        try {
            const body = await res.json()
            if (typeof body.detail === "string") {
                detail = body.detail
            } else if (Array.isArray(body.detail)) {
                detail = body.detail
                    .map((item: { loc?: unknown[]; msg?: string }) => {
                        const location = Array.isArray(item.loc)
                            ? item.loc
                                  .filter((part) => part !== "body")
                                  .join(".")
                            : ""
                        return location
                            ? `${location}: ${item.msg ?? "Invalid value"}`
                            : (item.msg ?? "Invalid request")
                    })
                    .join("; ")
            }
        } catch {
            // Fall back to the HTTP status when the server does not return JSON.
        }
        throw new Error(
            detail
                ? `Failed to load strategies: ${detail}`
                : `Failed to load strategies (${res.status})`,
        )
    }
    const data: StrategiesResult = await res.json()
    return data
}
