import { AreaSelection, Bounds, Point } from "../data/point"
import { API_URL } from "./apiUrl"
import { Field } from "./fetchField"
import { toSelectedField } from "./fetchFieldNdvi"

// GAEZ suitability classes, best first
export type SuitabilityClass =
    | "very_high"
    | "high"
    | "good"
    | "medium"
    | "moderate"
    | "marginal"
    | "very_marginal"
    | "not_suitable"

export type CropGroup =
    | "cereal"
    | "oilseed"
    | "pulse"
    | "sugar"
    | "root_tuber"
    | "vegetable"
    | "fodder"
    | "fibre"
    | "other"
    | "bioenergy"
    | "tree"

export type Crop = {
    id: string // crop name as a slug ("wheat", "dry_pea"), the ids /api/strategies uses
    code: string // GAEZ crop code, e.g. "whe"
    name: string
    group: CropGroup
    suitability: number // 0-100, averaged over the area
    class: SuitabilityClass
    yieldKgHa: number | null // attainable yield, averaged over the area
    yieldBasis: string | null // dry_weight | oil | sugar | lint
}

export type CropsResult = {
    crops: Crop[]
    cells: number // distinct GAEZ cells the area touches
}

// one plot's answer from POST /api/fields/suitability
type PlotSuitability = {
    id?: string
    cell?: { lat: number; lng: number; size_km: number }
    crops: {
        crop: string
        code: string
        group: CropGroup
        suitability: number
        class: SuitabilityClass
        attainable_yield_kg_ha: number | null
        yield_basis: string | null
    }[]
    error?: string
}

// field crops only: GAEZ also rates tree crops and energy grasses, which
// read oddly on arable land (olive scores well around Dobrich)
const ARABLE_GROUPS: CropGroup[] = [
    "cereal",
    "oilseed",
    "pulse",
    "sugar",
    "root_tuber",
    "vegetable",
    "fodder",
    "fibre",
    "other",
]

// suited to the area when its average reaches GAEZ "medium" (40 / 100)
export const MIN_SUITABILITY = 40
const CLASS_FLOORS: [number, SuitabilityClass][] = [
    [85, "very_high"],
    [70, "high"],
    [55, "good"],
    [40, "medium"],
    [25, "moderate"],
    [10, "marginal"],
    [0.01, "very_marginal"],
    [0, "not_suitable"],
]

function classOf(suitability: number): SuitabilityClass {
    return CLASS_FLOORS.find(([low]) => suitability >= low)![1]
}
const GAEZ_CELL_DEG = 1 / 12
const MAX_PLOTS = 50 // backend limit per request

// a rectangle is cut along the GAEZ grid, one plot per ~9 km cell it touches,
// so a large rectangle is judged everywhere and not just at one point
function boundsPlots({ topLeft, bottomRight }: Bounds): Point[][] {
    const firstRow = Math.floor((90 - topLeft.lat) / GAEZ_CELL_DEG)
    const lastRow = Math.floor((90 - bottomRight.lat) / GAEZ_CELL_DEG)
    const firstCol = Math.floor((topLeft.lng + 180) / GAEZ_CELL_DEG)
    const lastCol = Math.floor((bottomRight.lng + 180) / GAEZ_CELL_DEG)

    const plots: Point[][] = []
    for (let row = firstRow; row <= lastRow; row++) {
        for (let col = firstCol; col <= lastCol; col++) {
            const north = Math.min(topLeft.lat, 90 - row * GAEZ_CELL_DEG)
            const south = Math.max(
                bottomRight.lat,
                90 - (row + 1) * GAEZ_CELL_DEG,
            )
            const west = Math.max(topLeft.lng, col * GAEZ_CELL_DEG - 180)
            const east = Math.min(
                bottomRight.lng,
                (col + 1) * GAEZ_CELL_DEG - 180,
            )
            plots.push([
                { lat: north, lng: west },
                { lat: north, lng: east },
                { lat: south, lng: east },
                { lat: south, lng: west },
            ])
        }
    }
    // evenly thin out a huge rectangle to the per-request limit
    if (plots.length <= MAX_PLOTS) return plots
    const step = plots.length / MAX_PLOTS
    return Array.from(
        { length: MAX_PLOTS },
        (_, i) => plots[Math.floor(i * step)],
    )
}

function cropId(name: string): string {
    return name
        .toLowerCase()
        .replace(/[^a-z]+/g, "_")
        .replace(/^_|_$/g, "")
}

function requestBody(selection: AreaSelection): object {
    const plots =
        selection.kind === "bounds"
            ? { plots: boundsPlots(selection.bounds) }
            : { fields: selection.fields.slice(0, MAX_PLOTS) }
    return { ...plots, minClass: "not_suitable", groups: ARABLE_GROUPS }
}

// every plot carries every crop (minClass not_suitable), so each crop is
// averaged over the whole area; one poor spot doesn't knock a crop out
function combine(plots: PlotSuitability[]): CropsResult {
    const withData = plots.filter((p) => !p.error && p.cell)
    if (withData.length === 0) {
        const reason = plots.find((p) => p.error)?.error
        throw new Error(reason ?? "No suitability data for this area")
    }
    const cells = new Set(withData.map((p) => `${p.cell!.lat},${p.cell!.lng}`))

    type Sum = { crop: Crop; score: number; yieldSum: number; yields: number }
    const sums = new Map<string, Sum>()
    for (const plot of withData) {
        for (const c of plot.crops) {
            let sum = sums.get(c.code)
            if (!sum) {
                sum = {
                    crop: {
                        id: cropId(c.crop),
                        code: c.code,
                        name: c.crop,
                        group: c.group,
                        suitability: 0,
                        class: "not_suitable",
                        yieldKgHa: null,
                        yieldBasis: c.yield_basis,
                    },
                    score: 0,
                    yieldSum: 0,
                    yields: 0,
                }
                sums.set(c.code, sum)
            }
            sum.score += c.suitability
            if (c.attainable_yield_kg_ha != null) {
                sum.yieldSum += c.attainable_yield_kg_ha
                sum.yields += 1
            }
        }
    }
    const crops = Array.from(sums.values())
        .map(({ crop, score, yieldSum, yields }) => {
            // a crop missing from a plot has no data there: count it as 0
            const suitability = Math.round((score / withData.length) * 10) / 10
            return {
                ...crop,
                suitability,
                class: classOf(suitability),
                yieldKgHa: yields ? Math.round(yieldSum / yields) : null,
            }
        })
        .sort((a, b) => b.suitability - a.suitability)
    return { crops, cells: cells.size }
}

export function prefetchSuitability(fields: Field[]): void {
    const selection: AreaSelection = {
        kind: "fields",
        fields: fields.map(toSelectedField),
        areaHa: 0,
    }
    fetch(`${API_URL}/api/fields/suitability/prefetch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(requestBody(selection)),
    }).catch(() => {})
}

export async function fetchCrops(
    selection: AreaSelection,
    signal?: AbortSignal,
): Promise<CropsResult> {
    const res = await fetch(`${API_URL}/api/fields/suitability`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(requestBody(selection)),
        signal,
    })
    if (!res.ok) throw new Error(`Failed to load crops (${res.status})`)
    return combine(await res.json())
}
