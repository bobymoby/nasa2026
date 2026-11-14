import { useNavigate } from "react-router"
import "./LandingPage.css"
import Map from "../../components/Map/Map"
import { Bounds } from "../../data/point"

type Props = {
    bounds: Bounds | null
    onBoundsChange: (bounds: Bounds | null) => void
}

function LandingPage({ bounds, onBoundsChange }: Props) {
    const navigate = useNavigate()

    return (
        <Map
            bounds={bounds}
            onBoundsChange={onBoundsChange}
            onConfirm={() => navigate("/crops")}
        />
    )
}

export default LandingPage
