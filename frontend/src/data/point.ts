export type Point = { lat: number; lng: number }

// always normalised so topLeft is north-west of bottomRight
export type Bounds = { topLeft: Point; bottomRight: Point }

// what the crops page is for: a drawn rectangle or a set of picked fields
export type SelectedField = { id: string; polygon: Point[] }

export type AreaSelection =
    | { kind: "bounds"; bounds: Bounds }
    | { kind: "fields"; fields: SelectedField[]; areaHa: number }

// identifies the area in caches; fields by id, a rectangle by its corners
export function selectionKey(selection: AreaSelection): string {
    if (selection.kind === "fields") {
        return `fields:${selection.fields.map((f) => f.id).join(",")}`
    }
    const { topLeft, bottomRight } = selection.bounds
    return `bounds:${topLeft.lat},${topLeft.lng},${bottomRight.lat},${bottomRight.lng}`
}

export function describeSelection(selection: AreaSelection): string {
    if (selection.kind === "fields") {
        const count = selection.fields.length
        return `${count} field${count === 1 ? "" : "s"} (${selection.areaHa.toFixed(2)} ha)`
    }
    const { topLeft, bottomRight } = selection.bounds
    return (
        `top-left ${topLeft.lat.toFixed(4)}, ${topLeft.lng.toFixed(4)} · ` +
        `bottom-right ${bottomRight.lat.toFixed(4)}, ${bottomRight.lng.toFixed(4)}`
    )
}
