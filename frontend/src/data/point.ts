export type Point = { lat: number; lng: number }

// always normalised so topLeft is north-west of bottomRight
export type Bounds = { topLeft: Point; bottomRight: Point }

// what the crops page is for: a drawn rectangle or a set of picked fields
export type SelectedField = { id: string; polygon: Point[] }

export type AreaSelection =
    | { kind: "bounds"; bounds: Bounds }
    | { kind: "fields"; fields: SelectedField[]; areaHa: number }
