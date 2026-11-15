import { AreaSelection } from "../data/point"
import { API_URL } from "./apiUrl"

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

export type Money = {
    revenue: number
    seedCost: number
    operatingCost: number
    totalCost: number
    profit: number
}

// estimates in EUR; perHa is one pass through the rotation
export type Economics = {
    currency: "EUR"
    // ISO alpha-2 country whose market figures are used
    country: string
    // price normalization year for Eurostat/FAOSTAT reference prices
    priceYear: number | null
    // false when a crop lacks a published yield, price or cost estimate and is left out of the totals
    complete: boolean
    missingData: string[]
    areaHa: number
    perHa: Money
    profitPerHaYear: number
    // perHa scaled to the selected area
    total: Money
    plantings: {
        cropName: string
        role: PlantingRole
        yieldTHa: number | null
        priceEurT: number | null
        revenue: number
        cost: number
        profit: number
        // "price eurostat 2020–2024, yield eurostat 2021–2025", "no market data", "no cost estimate" or "no harvest"
        source: string
    }[]
    byYear: { year: number; revenue: number; cost: number; profit: number }[]
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
    dataMode:
        | "illustrative_mock"
        | "mixed_service_and_mock"
        | "mixed_dataset_and_mock"
    assumptions: string[]
    strategies: Strategy[]
}

export async function fetchStrategies(
    selection: AreaSelection,
    crops: string[],
    signal?: AbortSignal,
): Promise<StrategiesResult> {
    const area =
        selection.kind === "bounds"
            ? { bounds: selection.bounds }
            : { fields: selection.fields }
    const contextArea =
        selection.kind === "bounds"
            ? {
                  plots: [[
                      [selection.bounds.topLeft.lat, selection.bounds.topLeft.lng],
                      [selection.bounds.topLeft.lat, selection.bounds.bottomRight.lng],
                      [selection.bounds.bottomRight.lat, selection.bounds.bottomRight.lng],
                      [selection.bounds.bottomRight.lat, selection.bounds.topLeft.lng],
                  ]],
              }
            : { fields: selection.fields }

    let siteContext: unknown
    const contextWarnings: string[] = []
    try {
        const contextResponse = await fetch(
            `${API_URL}/api/fields/strategy-context`,
            {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(contextArea),
                signal,
            },
        )
        if (!contextResponse.ok) {
            const detail = await contextResponse.text().catch(() => "")
            contextWarnings.push(
                `Field data context request failed (${contextResponse.status})${detail ? `: ${detail}` : ""}`,
            )
        } else {
            siteContext = await contextResponse.json()
        }
    } catch (error) {
        if (signal?.aborted) throw error
        contextWarnings.push(
            `Field data context could not be fetched: ${error instanceof Error ? error.message : String(error)}`,
        )
    }

    const res = await fetch(`${API_URL}/api/strategies`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            crops,
            ...area,
            ...(siteContext ? { siteContext } : {}),
            contextWarnings,
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
