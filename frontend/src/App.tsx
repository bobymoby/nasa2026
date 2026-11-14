import { useState } from "react"
import {
    BrowserRouter,
    Navigate,
    Route,
    Routes,
    useNavigate,
} from "react-router"
import "./App.css"
import Map from "./Map"
import CropsPage from "./CropsPage"
import { Bounds } from "./point"

function AppRoutes() {
    const [bounds, setBounds] = useState<Bounds | null>(null)
    const [selectedCrops, setSelectedCrops] = useState<string[]>([])
    const navigate = useNavigate()

    return (
        <Routes>
            <Route
                path="/"
                element={
                    <Map
                        bounds={bounds}
                        onBoundsChange={setBounds}
                        onConfirm={() => navigate("/crops")}
                    />
                }
            />
            <Route
                path="/crops"
                element={
                    bounds ? (
                        <CropsPage
                            bounds={bounds}
                            selectedCrops={selectedCrops}
                            onSelectedCropsChange={setSelectedCrops}
                        />
                    ) : (
                        <Navigate to="/" replace />
                    )
                }
            />
        </Routes>
    )
}

function App() {
    return (
        <div className="app">
            <BrowserRouter>
                <AppRoutes />
            </BrowserRouter>
        </div>
    )
}

export default App
