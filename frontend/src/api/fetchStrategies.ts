import { AreaSelection } from "../data/point"
import { API_URL } from "./apiUrl"

// "unknown" when the data behind an assessment is unavailable
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
    // total over the whole planting; null without climate data
    waterUseMm: number | null
    traits: Trait[]
    note: string
}

export type Rating = { score: number; summary: string }
export type Assessment = { level: Level; summary: string }

export type EconomicsMoney = {
    revenue: number
    seedCost: number
    operatingCost: number
    totalCost: number
    profit: number
}

// Current backend contract matches the data returned by app.routers.strategies.
// Legacy compatibility aliases are kept to support older frontend code paths.
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
    perHa: EconomicsMoney
    profitPerHaYear: number
    total: EconomicsMoney
    plantings: {
        cropName: string
        role: PlantingRole
        yieldTHa: number | null
        priceEurT: number | null
        revenue: number
        cost: number
        profit: number
        // "price eurostat 2020–2024, yield eurostat 2021–2025"
        source: string
    }[]
    byYear: { year: number; revenue: number; cost: number; profit: number }[]
    // Legacy compatibility values for older code paths; computed when absent.
    revenuePerHa?: number
    revenuePerHaYear?: number
    totalRevenue?: number
}

export type Strategy = {
    id: string
    strategyType:
        | "planned"
        | "profit_first"
        | "soil_builder"
        | "risk_balanced"
        | "water_efficient"
        | "balanced"
    name: string
    summary: string
    durationYears: number
    // the requested crop ids this plan grows
    matchedCrops: string[]
    rotation: string[]
    // crops the plan adds to the selection for its goal
    addedCrops: string[]
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

function areaBody(selection: AreaSelection) {
    return selection.kind === "bounds"
        ? { bounds: selection.bounds }
        : { fields: selection.fields }
}

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
    if (!res.ok) {
        throw new Error(`Failed to load plannable crops (${res.status})`)
    }
    const data: { crops: string[] } = await res.json()
    return data.crops
}

function normalizeEconomics(
    economics: Economics,
    durationYears: number,
): Economics {
    const perHa = economics.perHa ?? {
        revenue: economics.revenuePerHa ?? 0,
        seedCost: 0,
        operatingCost: 0,
        totalCost: 0,
        profit: 0,
    }
    const total = economics.total ?? {
        revenue: economics.totalRevenue ?? 0,
        seedCost: 0,
        operatingCost: 0,
        totalCost: 0,
        profit: 0,
    }
    return {
        ...economics,
        perHa,
        total,
        revenuePerHa: economics.revenuePerHa ?? perHa.revenue,
        revenuePerHaYear:
            economics.revenuePerHaYear ??
            (durationYears > 0 ? perHa.revenue / durationYears : perHa.revenue),
        totalRevenue: economics.totalRevenue ?? total.revenue,
    }
}

// plannedCrops (planting order) come back first as the "planned" strategy
export async function fetchStrategies(
    selection: AreaSelection,
    crops: string[],
    plannedCrops: string[],
    signal?: AbortSignal,
): Promise<StrategiesResult> {
    const area = areaBody(selection)
    const res = await fetch(`${API_URL}/api/strategies`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ crops, plannedCrops, ...area }),
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
    return {
        ...data,
        strategies: data.strategies.map((strategy) => ({
            ...strategy,
            economics: normalizeEconomics(
                strategy.economics,
                strategy.durationYears,
            ),
        })),
    }
}
