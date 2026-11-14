import React, { useCallback, useMemo, useState } from "react"
import ReactDOM from "react-dom/client"
import "./index.css"
import LandingPage from "./pages/LandingPage/LandingPage"
import { BrowserRouter, Navigate, Route, Routes } from "react-router"
import CropsPage from "./pages/CropsPage/CropsPage"
import { AreaSelection, Bounds } from "./data/point"
import { Field } from "./api/fetchField"

const root = ReactDOM.createRoot(document.getElementById("root") as HTMLElement)

function AppRoutes() {
    const [bounds, setBounds] = useState<Bounds | null>(null)
    const [selectedCrops, setSelectedCrops] = useState<string[]>([])
    const [selectedFields, setSelectedFields] = useState<Field[]>([])

    // the crops page gets one or the other, so drawing a rectangle drops the
    // picked fields and picking a field drops the rectangle
    const changeBounds = useCallback((next: Bounds | null) => {
        setBounds(next)
        if (next) setSelectedFields([])
    }, [])
    const changeSelectedFields = useCallback((next: Field[]) => {
        setSelectedFields(next)
        if (next.length > 0) setBounds(null)
    }, [])

    // memoised so the crops page doesn't refetch on unrelated re-renders
    const selection = useMemo<AreaSelection | null>(() => {
        if (selectedFields.length > 0) {
            return {
                kind: "fields",
                fields: selectedFields.map((f) => ({
                    id: f.id,
                    polygon: f.geometry.coordinates[0].map(([lng, lat]) => ({
                        lat,
                        lng,
                    })),
                })),
                areaHa: selectedFields.reduce(
                    (sum, f) => sum + f.properties.area_ha,
                    0,
                ),
            }
        }
        return bounds && { kind: "bounds", bounds }
    }, [bounds, selectedFields])

    return (
        <Routes>
            <Route
                path="/"
                element={
                    <LandingPage
                        bounds={bounds}
                        onBoundsChange={changeBounds}
                        selectedFields={selectedFields}
                        onSelectedFieldsChange={changeSelectedFields}
                    />
                }
            />
            <Route
                path="/crops"
                element={
                    selection ? (
                        <CropsPage
                            selection={selection}
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
