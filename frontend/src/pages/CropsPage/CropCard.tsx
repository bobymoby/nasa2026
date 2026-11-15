import { memo } from "react"

import SoilImpactCard from "./SoilImpactCard"
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
    const { gaez, soil } = crop
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
                    {gaez ? (
                        <span className={`crop-class class-${gaez.class}`}>
                            {CLASS_LABELS[gaez.class]}
                        </span>
                    ) : (
                        <span className="crop-class">Not rated</span>
                    )}
                </span>

                <span className="crop-stats">
                    <span
                        className="crop-stat"
                        title="FAO GAEZ v4 suitability for rainfed farming, averaged over the area"
                    >
                        <span className="crop-stat-value">
                            {gaez ? Math.round(gaez.suitability) : "–"}
                        </span>
                        <span className="crop-stat-text">
                            <span>Suitability</span>
                            <span>
                                {gaez
                                    ? (formatYield(gaez) ?? "/ 100")
                                    : "not rated by GAEZ"}
                            </span>
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

export default CropCard
