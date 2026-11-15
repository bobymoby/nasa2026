import { AreaSelection } from "../data/point"
import { API_URL } from "./apiUrl"

export type Level = "low" | "medium" | "high"
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
    // prices are real euros of this year
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

export async function fetchStrategies(
    selection: AreaSelection,
    crops: string[],
    signal?: AbortSignal,
): Promise<Strategy[]> {
    const area =
        selection.kind === "bounds"
            ? { bounds: selection.bounds }
            : { fields: selection.fields }
    const res = await fetch(`${API_URL}/api/strategies`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ crops, ...area }),
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
    const data: { strategies: Strategy[] } = await res.json()
    return data.strategies
}
