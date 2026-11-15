import { memo } from "react"

import { Crop } from "../../api/fetchCrops"
import SoilImpactCard from "./SoilImpactCard"

// memoised: the grid holds every crop, and ticking one box or typing in the
// search must not re-render all of them
const CropCard = memo(function CropCard({
    crop,
    checked,
    onToggle,
}: {
    crop: Crop
    checked: boolean
    onToggle: (id: string) => void
}) {
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
                    {crop.soil.family && (
                        <span className="crop-family">{crop.soil.family}</span>
                    )}
                </span>
                <SoilImpactCard soil={crop.soil} />
            </label>
        </li>
    )
})

export default CropCard
