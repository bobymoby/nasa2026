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
    const [plannedCrops, setPlannedCrops] = useState<string[]>([])
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
                                to: "/planned",
                                label: "Build strategies →",
                            }}
                        />
                    ) : (
                        <Navigate to="/" replace />
                    )
                }
            />
            <Route
                path="/planned"
                element={
                    !selection ? (
                        <Navigate to="/" replace />
                    ) : selectedCrops.length === 0 ? (
                        <Navigate to="/crops" replace />
                    ) : (
                        <CropsPage
                            key="planned"
                            selection={selection}
                            selectedCrops={plannedCrops}
                            onSelectedCropsChange={setPlannedCrops}
                            onlyCrops={selectedCrops}
                            ordered
                            title="Planned rotation"
                            intro="From the crops you just picked, click the ones you plan to grow in the order you will plant them, then drag the cards to change the order. Your planned rotation is shown first and is the baseline the other strategies are compared with."
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
                            plannedCrops={plannedCrops}
                        />
                    )
                }
            />
        </Routes>
    )
}
