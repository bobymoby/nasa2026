import { Level, MonthRef, PlantingRole, Trait } from "../../api/fetchStrategies"

export const MONTHS = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]

export const ROLE_LABELS: Record<PlantingRole, string> = {
    cash: "Cash crop",
    forage: "Forage",
    cover: "Cover crop",
}

export const TRAIT_LABELS: Record<Trait, string> = {
    nitrogen_fixing: "Nitrogen-fixing",
    deep_rooting: "Deep-rooting",
    drought_tolerant: "Drought-tolerant",
    cover: "Soil cover",
}

export const LEVEL_LABELS: Record<Level, string> = {
    low: "Low",
    medium: "Medium",
    high: "High",
    unknown: "Unknown",
}

export function formatMonth({ year, month }: MonthRef): string {
    return `${MONTHS[month - 1]}, year ${year}`
}

// "sunflower" -> "Sunflower"
export function humanize(id: string): string {
    const words = id.replace(/_/g, " ")
    return words.charAt(0).toUpperCase() + words.slice(1)
}
