import { useNavigate } from "react-router"
import "./LandingPage.css"
import Map from "../../components/Map/Map"
import { Bounds } from "../../data/point"
import { Field } from "../../api/fetchField"

type Props = {
    bounds: Bounds | null
    onBoundsChange: (bounds: Bounds | null) => void
    selectedFields: Field[]
    onSelectedFieldsChange: (fields: Field[]) => void
}

function LandingPage({
    bounds,
    onBoundsChange,
    selectedFields,
    onSelectedFieldsChange,
}: Props) {
    const navigate = useNavigate()

    return (
        <Map
            bounds={bounds}
            onBoundsChange={onBoundsChange}
            selectedFields={selectedFields}
            onSelectedFieldsChange={onSelectedFieldsChange}
            onConfirm={() => navigate("/crops")}
        />
    )
}

export default LandingPage
