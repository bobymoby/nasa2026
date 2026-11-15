import {
    Crop,
    CropGroup,
    SoilImpact,
    SoilScoredCrop,
    SuitabilityClass,
} from "../../api/fetchCrops"

export type ListedCrop = {
    id: string
    name: string
    gaez: Crop | null
    soil: SoilImpact | null
    searchText: string
}

export const CLASS_LABELS: Record<SuitabilityClass, string> = {
    very_high: "Very high",
    high: "High",
    good: "Good",
    medium: "Medium",
    moderate: "Moderate",
    marginal: "Marginal",
    very_marginal: "Very marginal",
    not_suitable: "Not suitable",
}

export const GROUP_LABELS: Record<CropGroup, string> = {
    cereal: "Cereals",
    oilseed: "Oilseeds",
    pulse: "Pulses",
    sugar: "Sugar crops",
    root_tuber: "Roots and tubers",
    vegetable: "Vegetables",
    fodder: "Fodder",
    fibre: "Fibre",
    other: "Other",
    bioenergy: "Bioenergy",
    tree: "Tree crops",
}

const YIELD_BASIS: Record<string, string> = {
    dry_weight: "",
    oil: " oil",
    sugar: " sugar",
    lint: " lint",
}

export function formatYield(crop: Crop): string | null {
    if (crop.yieldKgHa == null || crop.yieldKgHa <= 0) return null
    const basis = YIELD_BASIS[crop.yieldBasis ?? ""] ?? ""
    return `up to ${(crop.yieldKgHa / 1000).toFixed(1)} t/ha${basis}`
}

function listed(
    id: string,
    name: string,
    gaez: Crop | null,
    soil: SoilImpact | null,
): ListedCrop {
    const searchText = [
        name,
        gaez && GROUP_LABELS[gaez.group],
        soil?.family,
        soil?.note,
    ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase()
    return { id, name, gaez, soil, searchText }
}

// GAEZ crops keep their ids (shared with /api/strategies and the price
// pipeline); soil-scored crops GAEZ doesn't rate are added under their
// crop_soil_scores key
export function mergeCrops(
    gaez: Crop[] | null,
    soil: SoilScoredCrop[] | null,
): ListedCrop[] {
    const soilByCode = new Map<string, SoilScoredCrop>()
    for (const s of soil ?? []) {
        for (const code of s.gaezCodes) soilByCode.set(code, s)
    }
    const rated = gaez ?? []
    const ratedCodes = new Set(rated.map((c) => c.code))
    return [
        ...rated.map((c) =>
            listed(c.id, c.name, c, soilByCode.get(c.code)?.soil ?? null),
        ),
        ...(soil ?? [])
            .filter((s) => !s.gaezCodes.some((code) => ratedCodes.has(code)))
            .map((s) => listed(s.id, s.name, null, s.soil)),
    ]
}
