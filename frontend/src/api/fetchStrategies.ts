import { AreaSelection } from "../data/point"
import { API_URL } from "./apiUrl"

export type Level = "low" | "medium" | "high"
export type PlantingRole = "cash" | "cover" | "forage"
export type Trait =
    | "nitrogen_fixing"
    | "deep_rooting"
    | "drought_tolerant"
    | "cover"

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
    if (!res.ok) throw new Error(`Failed to load strategies (${res.status})`)
    const data: { strategies: Strategy[] } = await res.json()
    return data.strategies
}
