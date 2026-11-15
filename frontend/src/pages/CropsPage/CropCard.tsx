import { DragEvent, memo } from "react"

import SoilImpactCard from "./SoilImpactCard"
import { CLASS_LABELS, formatYield, ListedCrop } from "./listedCrops"

// memoised: the grid holds every crop, so ticking one box or typing in the
// search must not re-render all of them
const CropCard = memo(function CropCard({
    crop,
    checked,
    order,
    onToggle,
    drag,
    dragging = false,
    dropSide,
}: {
    crop: ListedCrop
    checked: boolean
    // planting position, shown instead of a tick when the order matters
    order?: number
    onToggle: (id: string) => void
    // drag-and-drop reordering of the planting sequence
    drag?: CardDrag
    dragging?: boolean
    dropSide?: DropSide
}) {
    const { gaez, soil } = crop
    const over = (e: DragEvent<HTMLLIElement>) => {
        e.preventDefault()
        const { left, width } = e.currentTarget.getBoundingClientRect()
        drag?.over(crop.id, e.clientX > left + width / 2 ? "after" : "before")
    }
    return (
        <li
            className={
                [dragging && "dragging", dropSide && `drop-${dropSide}`]
                    .filter(Boolean)
                    .join(" ") || undefined
            }
            {...(drag && {
                draggable: true,
                onDragStart: (e: DragEvent<HTMLLIElement>) => {
                    e.dataTransfer.effectAllowed = "move"
                    e.dataTransfer.setData("text/plain", crop.id)
                    drag.start(crop.id)
                },
                onDragOver: over,
                onDrop: (e: DragEvent<HTMLLIElement>) => {
                    e.preventDefault()
                    drag.drop()
                },
                onDragEnd: drag.end,
            })}
        >
            <label>
                <span className="crop-head">
                    <input
                        type="checkbox"
                        className={
                            order !== undefined ? "crop-order-input" : undefined
                        }
                        checked={checked}
                        onChange={() => onToggle(crop.id)}
                    />
                    {order !== undefined && (
                        <span className="crop-order" aria-hidden="true">
                            {order || ""}
                        </span>
                    )}
                    <span className="crop-name" title={crop.name}>
                        {crop.name}
                    </span>
                    <span className={`crop-class class-${gaez.class}`}>
                        {CLASS_LABELS[gaez.class]}
                    </span>
                </span>

                <span className="crop-stats">
                    <span
                        className="crop-stat"
                        title="FAO GAEZ v4 suitability for rainfed farming, averaged over the area"
                    >
                        <span className="crop-stat-value">
                            {Math.round(gaez.suitability)}
                        </span>
                        <span className="crop-stat-text">
                            <span>Suitability</span>
                            <span>{formatYield(gaez) ?? "/ 100"}</span>
                        </span>
                    </span>
                    <span
                        className="crop-stat"
                        title="How the crop treats the soil (crop_soil_scores)"
                    >
                        <span className="crop-stat-value">
                            {soil?.score ?? "–"}
                        </span>
                        <span className="crop-stat-text">
                            <span>Soil health</span>
                            <span>{soil ? "/ 100" : "no soil data"}</span>
                        </span>
                    </span>
                </span>

                {soil && <SoilImpactCard soil={soil} />}
            </label>
        </li>
    )
})

export type DropSide = "before" | "after"

export type CardDrag = {
    start: (id: string) => void
    over: (id: string, side: DropSide) => void
    drop: () => void
    end: () => void
}

export default CropCard
