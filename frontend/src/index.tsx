import React, { useState } from "react"
import ReactDOM from "react-dom/client"
import "./index.css"
import LandingPage from "./pages/LandingPage/LandingPage"
import { BrowserRouter, Navigate, Route, Routes } from "react-router"
import CropsPage from "./pages/CropsPage/CropsPage"
import { Bounds } from "./data/point"
import { Field } from "./api/fetchField"

const root = ReactDOM.createRoot(document.getElementById("root") as HTMLElement)

function AppRoutes() {
    const [bounds, setBounds] = useState<Bounds | null>(null)
    const [selectedCrops, setSelectedCrops] = useState<string[]>([])
    const [selectedFields, setSelectedFields] = useState<Field[]>([])

    return (
        <Routes>
            <Route
                path="/"
                element={
                    <LandingPage
                        bounds={bounds}
                        onBoundsChange={setBounds}
                        selectedFields={selectedFields}
                        onSelectedFieldsChange={setSelectedFields}
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

root.render(
    <React.StrictMode>
        <div className="app">
            <BrowserRouter>
                <AppRoutes />
            </BrowserRouter>
        </div>
    </React.StrictMode>,
)
