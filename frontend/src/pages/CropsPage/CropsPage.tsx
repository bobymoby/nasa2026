import "./CropsPage.scss"
import {
    useCallback,
    useDeferredValue,
    useEffect,
    useMemo,
    useRef,
    useState,
} from "react"
import { Link } from "react-router"

import {
    CropsResult,
    fetchCrops,
    fetchSoilScores,
    MIN_SUITABILITY,
    SoilScoredCrop,
} from "../../api/fetchCrops"
import { AreaSelection, describeSelection } from "../../data/point"
import CropCard from "./CropCard"
import ScoreInfo from "./ScoreInfo"
import { ListedCrop, mergeCrops } from "./listedCrops"
import { useSmoothHorizontalScroll } from "./useSmoothHorizontalScroll"

// smallest height a card fits in; keep in sync with the card styles
const MIN_CARD_HEIGHT = 250
const GRID_GAP = 12
const FALLBACK_COUNT = 12

type SortKey = "suitability" | "soil" | "name"

const SORTERS: Record<SortKey, (a: ListedCrop, b: ListedCrop) => number> = {
    suitability: (a, b) =>
        (b.gaez?.suitability ?? -1) - (a.gaez?.suitability ?? -1) ||
        (b.soil?.score ?? -1) - (a.soil?.score ?? -1),
    soil: (a, b) => (b.soil?.score ?? -1) - (a.soil?.score ?? -1),
    name: (a, b) => a.name.localeCompare(b.name),
}

type Props = {
    selection: AreaSelection
    selectedCrops: string[]
    onSelectedCropsChange: (ids: string[]) => void
}

function CropsPage({ selection, selectedCrops, onSelectedCropsChange }: Props) {
    const [gaez, setGaez] = useState<CropsResult | null>(null)
    const [gaezError, setGaezError] = useState<string | null>(null)
    const [soil, setSoil] = useState<SoilScoredCrop[] | null>(null)
    const [soilError, setSoilError] = useState<string | null>(null)
    const [reloadKey, setReloadKey] = useState(0)
    const [query, setQuery] = useState("")
    const [sort, setSort] = useState<SortKey>("suitability")
    const deferredQuery = useDeferredValue(query)

    useEffect(() => {
        const controller = new AbortController()
        setGaez(null)
        setGaezError(null)
        fetchCrops(selection, controller.signal)
            .then(setGaez)
            .catch((err) => {
                if (controller.signal.aborted) return
                setGaezError(err instanceof Error ? err.message : String(err))
            })
        return () => controller.abort()
    }, [selection, reloadKey])

    useEffect(() => {
        const controller = new AbortController()
        setSoilError(null)
        fetchSoilScores(controller.signal)
            .then(setSoil)
            .catch((err) => {
                if (controller.signal.aborted) return
                setSoilError(err instanceof Error ? err.message : String(err))
            })
        return () => controller.abort()
    }, [reloadKey])

    const all = useMemo(
        () => mergeCrops(gaez?.crops ?? null, soil),
        [gaez, soil],
    )

    // drop picked crops not offered for this area; wait for both sources so
    // nothing is dropped while one is still loading
    useEffect(() => {
        if (!gaez || !soil) return
        const available = new Set(all.map((c) => c.id))
        const kept = selectedCrops.filter((id) => available.has(id))
        if (kept.length !== selectedCrops.length) onSelectedCropsChange(kept)
    }, [all, gaez, soil, selectedCrops, onSelectedCropsChange])

    // where nothing reaches "medium", offer the best-rated crops anyway so
    // there is always something to plan with
    const { shown, fallback } = useMemo(() => {
        const rated = all.filter((c) => c.gaez)
        const suited = rated.filter(
            (c) => c.gaez!.suitability >= MIN_SUITABILITY,
        )
        if (suited.length > 0 || rated.length === 0) {
            return { shown: suited, fallback: false }
        }
        const best = [...rated]
            .sort((a, b) => b.gaez!.suitability - a.gaez!.suitability)
            .slice(0, FALLBACK_COUNT)
        return { shown: best, fallback: true }
    }, [all])

    const filtered = useMemo(() => {
        const words = deferredQuery.toLowerCase().split(/\s+/).filter(Boolean)
        return shown
            .filter((c) => words.every((w) => c.searchText.includes(w)))
            .sort(SORTERS[sort])
    }, [shown, deferredQuery, sort])

    const ready = gaez !== null

    const scrollerRef = useRef<HTMLDivElement>(null)
    const scroller = useSmoothHorizontalScroll(scrollerRef, [ready])

    // CSS can't count rows against a flexible height, so measure it
    const [rows, setRows] = useState(1)
    useEffect(() => {
        const el = scrollerRef.current
        if (!el) return
        const measure = () => {
            const style = getComputedStyle(el)
            const height =
                el.clientHeight -
                parseFloat(style.paddingTop) -
                parseFloat(style.paddingBottom)
            setRows(
                Math.max(
                    1,
                    Math.floor(
                        (height + GRID_GAP) / (MIN_CARD_HEIGHT + GRID_GAP),
                    ),
                ),
            )
        }
        const observer = new ResizeObserver(measure)
        observer.observe(el)
        measure()
        return () => observer.disconnect()
    }, [ready])

    const { reset } = scroller
    useEffect(() => reset(), [deferredQuery, sort, reset])

    const scrollPage = (direction: 1 | -1) => {
        const width = scrollerRef.current?.clientWidth ?? 0
        scroller.scrollBy(direction * width * 0.9)
    }

    const byId = useMemo(() => new Map(all.map((c) => [c.id, c])), [all])

    // stable so the memoised cards don't all re-render when one is ticked
    const selectedRef = useRef(selectedCrops)
    selectedRef.current = selectedCrops
    const onChangeRef = useRef(onSelectedCropsChange)
    onChangeRef.current = onSelectedCropsChange
    const toggle = useCallback((id: string) => {
        const current = selectedRef.current
        onChangeRef.current(
            current.includes(id)
                ? current.filter((c) => c !== id)
                : [...current, id],
        )
    }, [])
    const selectedSet = useMemo(() => new Set(selectedCrops), [selectedCrops])

    const errors = [gaezError, soilError].filter(Boolean)
    const cells = gaez
        ? gaez.cells === 1
            ? "one ~9 km grid cell"
            : `averaged over ${gaez.cells} ~9 km grid cells`
        : "~9 km grid cells"

    return (
        <div className="crops-page">
            <header className="crops-header">
                <Link to="/" className="crops-back">
                    ← Back to map
                </Link>
                <h1>Select crops</h1>
                <ScoreInfo cells={cells} />
                <p className="crops-area">
                    Area: {describeSelection(selection)}
                </p>
            </header>

            {errors.length > 0 && (
                <div className="crops-error">
                    {errors.join(" · ")}
                    <button onClick={() => setReloadKey((k) => k + 1)}>
                        Retry
                    </button>
                </div>
            )}

            <div className="crops-toolbar">
                <input
                    type="search"
                    className="crops-search"
                    placeholder="Search by name, group, family or note…"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    autoFocus
                />
                <label className="crops-sort">
                    Sort
                    <select
                        value={sort}
                        onChange={(e) => setSort(e.target.value as SortKey)}
                    >
                        <option value="suitability">Suitability</option>
                        <option value="soil">Soil health</option>
                        <option value="name">Name, A–Z</option>
                    </select>
                </label>
                {ready && (
                    <span className="crops-results">
                        {filtered.length === shown.length
                            ? `${shown.length} crops`
                            : `${filtered.length} of ${shown.length} match`}
                    </span>
                )}
                <span className="crops-arrows">
                    <button
                        type="button"
                        aria-label="Scroll left"
                        disabled={!ready || scroller.atStart}
                        onClick={() => scrollPage(-1)}
                    >
                        ‹
                    </button>
                    <button
                        type="button"
                        aria-label="Scroll right"
                        disabled={!ready || scroller.atEnd}
                        onClick={() => scrollPage(1)}
                    >
                        ›
                    </button>
                </span>
            </div>

            {!ready && !gaezError && (
                <p className="crops-loading">
                    Checking which crops suit this area… a new area takes up to
                    ~10 s.
                </p>
            )}
            {ready && fallback && (
                <p className="crops-fallback">
                    No crop is rated medium ({MIN_SUITABILITY} / 100) or better
                    for this area, so the {shown.length} best-rated crops are
                    shown. Expect lower yields than the suitability scale
                    suggests for a good site.
                </p>
            )}
            {ready && filtered.length === 0 && (
                <p className="crops-empty">
                    {shown.length === 0
                        ? "No field crops are rated suitable for this area."
                        : `No crops match “${deferredQuery}”.`}
                </p>
            )}

            {ready ? (
                <div className="crops-scroller" ref={scrollerRef}>
                    <ul
                        className="crops-list"
                        style={{
                            gridTemplateRows: `repeat(${rows}, minmax(${MIN_CARD_HEIGHT}px, 1fr))`,
                        }}
                    >
                        {filtered.map((crop) => (
                            <CropCard
                                key={crop.id}
                                crop={crop}
                                checked={selectedSet.has(crop.id)}
                                onToggle={toggle}
                            />
                        ))}
                    </ul>
                </div>
            ) : (
                <div className="crops-scroller-placeholder" />
            )}

            <div className="crops-footer">
                <div className="crops-selected">
                    <span className="crops-count">
                        {selectedCrops.length} selected
                    </span>
                    {selectedCrops.map((id) => (
                        <button
                            key={id}
                            type="button"
                            className="crops-chip"
                            onClick={() => toggle(id)}
                            aria-label={`Remove ${byId.get(id)?.name ?? id}`}
                        >
                            {byId.get(id)?.name ?? id}
                            <span aria-hidden="true">×</span>
                        </button>
                    ))}
                </div>
                {selectedCrops.length > 0 ? (
                    <Link to="/strategy" className="crops-next">
                        Build strategies →
                    </Link>
                ) : (
                    <span className="crops-next disabled">
                        Pick a crop to continue
                    </span>
                )}
            </div>
        </div>
    )
}

export default CropsPage
