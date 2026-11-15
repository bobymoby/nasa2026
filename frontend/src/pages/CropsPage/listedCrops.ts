import {
    Crop,
    CropGroup,
    SuitabilityClass,
} from "../../api/fetchCrops"

export type ListedCrop = {
    id: string
    name: string
    gaez: Crop
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

export function listCrops(crops: Crop[] | null): ListedCrop[] {
    return (crops ?? []).map((gaez) => ({
        id: gaez.id,
        name: gaez.name,
        gaez,
        searchText: `${gaez.name} ${GROUP_LABELS[gaez.group]}`.toLowerCase(),
    }))
}
