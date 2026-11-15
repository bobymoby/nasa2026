import { memo } from "react"

import { CLASS_LABELS, formatYield, ListedCrop } from "./listedCrops"

// memoised: the grid holds every crop, so ticking one box or typing in the
// search must not re-render all of them
const CropCard = memo(function CropCard({
    crop,
    checked,
    onToggle,
}: {
    crop: ListedCrop
    checked: boolean
    onToggle: (id: string) => void
}) {
    const { gaez } = crop
    return (
        <li>
            <label>
                <span className="crop-head">
                    <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => onToggle(crop.id)}
                    />
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
                </span>
            </label>
        </li>
    )
})

export default CropCard
