export type Point = { lat: number; lng: number }

// always normalised so topLeft is north-west of bottomRight
export type Bounds = { topLeft: Point; bottomRight: Point }
