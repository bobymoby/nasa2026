import { useEffect, useRef, useState } from "react"

import { MIN_SUITABILITY } from "../../api/fetchCrops"

function ScoreInfo({ cells }: { cells: string }) {
    const [open, setOpen] = useState(false)
    const ref = useRef<HTMLDivElement>(null)

    useEffect(() => {
        if (!open) return
        const onPointerDown = (e: PointerEvent) => {
            if (!ref.current?.contains(e.target as Node)) setOpen(false)
        }
        const onKeyDown = (e: KeyboardEvent) => {
            if (e.key === "Escape") setOpen(false)
        }
        document.addEventListener("pointerdown", onPointerDown)
        document.addEventListener("keydown", onKeyDown)
        return () => {
            document.removeEventListener("pointerdown", onPointerDown)
            document.removeEventListener("keydown", onKeyDown)
        }
    }, [open])

    return (
        <div className="crops-info" ref={ref}>
            <button
                type="button"
                aria-expanded={open}
                onClick={() => setOpen((o) => !o)}
            >
                How scores work
            </button>
            {open && (
                <div className="crops-info-popover" role="dialog">
                    <h4>Suitability</h4>
                    <p>
                        FAO GAEZ v4 rating for rainfed farming (1981–2010
                        climate, high input level; {cells}). “Suited to this
                        area” lists crops rated at least medium (
                        {MIN_SUITABILITY} / 100). “All GAEZ crops” lets you
                        browse the complete set.
                    </p>
                    <h4>Soil profile</h4>
                    <p>
                        SoilGrids estimates for the selected area; these
                        measurements are not crop-specific.
                    </p>
                </div>
            )}
        </div>
    )
}

export default ScoreInfo
