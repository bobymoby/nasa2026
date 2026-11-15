import { useNavigate } from "react-router"
import Map from "../../components/Map/Map"
import { AreaSelection, Bounds } from "../../data/point"
import { Field } from "../../api/fetchField"

type Props = {
    bounds: Bounds | null
    onBoundsChange: (bounds: Bounds | null) => void
    selectedFields: Field[]
    onSelectedFieldsChange: (fields: Field[]) => void
    selection: AreaSelection | null
}

function LandingPage({
    bounds,
    onBoundsChange,
    selectedFields,
    onSelectedFieldsChange,
    selection,
}: Props) {
    const navigate = useNavigate()

    return (
        <Map
            bounds={bounds}
            onBoundsChange={onBoundsChange}
            selectedFields={selectedFields}
            onSelectedFieldsChange={onSelectedFieldsChange}
            onConfirm={() => navigate("/crops")}
            selection={selection}
        />
    )
}

export default LandingPage
