import { Navigate, Route, Routes } from "react-router"
import LandingPage from "./pages/LandingPage/LandingPage"
import CropsPage from "./pages/CropsPage/CropsPage"
import { useCallback, useMemo, useState } from "react"
import { Field } from "./api/fetchField"
import { AreaSelection, Bounds } from "./data/point"
import StrategyPage from "./pages/StrategyPage/StrategyPage"

export default function AppRouter() {
    const [bounds, setBounds] = useState<Bounds | null>(null)
    const [selectedCrops, setSelectedCrops] = useState<string[]>([])
    const [currentCrops, setCurrentCrops] = useState<string[]>([])
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

    // memoised so the map and crops page don't refetch on unrelated re-renders
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
                        selection={selection}
                    />
                }
            />
            <Route
                path="/crops"
                element={
                    selection ? (
                        <CropsPage
                            key="crops"
                            selection={selection}
                            selectedCrops={selectedCrops}
                            onSelectedCropsChange={setSelectedCrops}
                            next={{
                                to: "/current",
                                label: "Build strategies →",
                            }}
                        />
                    ) : (
                        <Navigate to="/" replace />
                    )
                }
            />
            <Route
                path="/current"
                element={
                    !selection ? (
                        <Navigate to="/" replace />
                    ) : selectedCrops.length === 0 ? (
                        <Navigate to="/crops" replace />
                    ) : (
                        <CropsPage
                            key="current"
                            selection={selection}
                            selectedCrops={currentCrops}
                            onSelectedCropsChange={setCurrentCrops}
                            title="Your current rotation"
                            intro="Pick the crops you grow now, in the order you plant them. Your current rotation is shown first, so you can compare it with the new strategies."
                            back={{ to: "/crops", label: "← Back to crops" }}
                            next={{
                                to: "/strategy",
                                label: "Build strategies →",
                                emptyLabel: "Skip, build strategies →",
                            }}
                        />
                    )
                }
            />
            <Route
                path="/strategy"
                element={
                    !selection ? (
                        <Navigate to="/" replace />
                    ) : selectedCrops.length === 0 ? (
                        <Navigate to="/crops" replace />
                    ) : (
                        <StrategyPage
                            selection={selection}
                            crops={selectedCrops}
                            currentCrops={currentCrops}
                        />
                    )
                }
            />
        </Routes>
    )
}
