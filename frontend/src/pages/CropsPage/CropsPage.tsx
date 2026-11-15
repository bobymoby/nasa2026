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

import { Crop, fetchCrops } from "../../api/fetchCrops"
import { AreaSelection, describeSelection } from "../../data/point"
import CropCard from "./CropCard"
import { useSmoothHorizontalScroll } from "./useSmoothHorizontalScroll"

// smallest height a card fits in; keep in sync with the card styles
const MIN_CARD_HEIGHT = 250
const GRID_GAP = 12

type SortKey = "score" | "name"

// every word must appear somewhere in the name, family or note
function matches(crop: Crop, words: string[]): boolean {
    const text =
        `${crop.name} ${crop.soil.family ?? ""} ${crop.soil.note ?? ""}`.toLowerCase()
    return words.every((w) => text.includes(w))
}

type Props = {
    selection: AreaSelection
    selectedCrops: string[]
    onSelectedCropsChange: (ids: string[]) => void
}

function CropsPage({
    selection,
    selectedCrops,
    onSelectedCropsChange,
}: Props) {
    const [crops, setCrops] = useState<Crop[] | null>(null)
    const [error, setError] = useState<string | null>(null)
    const [reloadKey, setReloadKey] = useState(0)
    const [query, setQuery] = useState("")
    const [sort, setSort] = useState<SortKey>("score")
    // keeps typing responsive while the list re-filters
    const deferredQuery = useDeferredValue(query)

    useEffect(() => {
        const controller = new AbortController()
        setCrops(null)
        setError(null)

        fetchCrops(selection, controller.signal)
            .then(setCrops)
            .catch((err) => {
                if (controller.signal.aborted) return
                setError(
                    err instanceof Error ? err.message : "Failed to load crops",
                )
            })

        return () => controller.abort()
    }, [selection, reloadKey])

    // drop previously picked crops that aren't available for this area
    useEffect(() => {
        if (!crops) return
        const available = new Set(crops.map((c) => c.id))
        const kept = selectedCrops.filter((id) => available.has(id))
        if (kept.length !== selectedCrops.length) onSelectedCropsChange(kept)
    }, [crops, selectedCrops, onSelectedCropsChange])

    const filtered = useMemo(() => {
        if (!crops) return []
        const words = deferredQuery.toLowerCase().split(/\s+/).filter(Boolean)
        const found = crops.filter((c) => matches(c, words))
        return sort === "score"
            ? found.sort((a, b) => (b.soil.score ?? 0) - (a.soil.score ?? 0))
            : found.sort((a, b) => a.name.localeCompare(b.name))
    }, [crops, deferredQuery, sort])

    const scrollerRef = useRef<HTMLDivElement>(null)
    const scroller = useSmoothHorizontalScroll(scrollerRef, [crops])

    // as many rows as fit the height left for the grid; CSS can't count rows
    // against a flexible height, so measure it
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
                    Math.floor((height + GRID_GAP) / (MIN_CARD_HEIGHT + GRID_GAP)),
                ),
            )
        }
        const observer = new ResizeObserver(measure)
        observer.observe(el)
        measure()
        return () => observer.disconnect()
    }, [crops])

    // start from the beginning of the grid whenever it changes
    const { reset } = scroller
    useEffect(() => reset(), [deferredQuery, sort, reset])

    const scrollPage = (direction: 1 | -1) => {
        const width = scrollerRef.current?.clientWidth ?? 0
        scroller.scrollBy(direction * width * 0.9)
    }

    const byId = useMemo(
        () => new Map((crops ?? []).map((c) => [c.id, c])),
        [crops],
    )

    // stable across renders so the memoised cards don't all re-render when
    // one is ticked; reads the latest selection through refs
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

    return (
        <div className="crops-page">
            <header className="crops-header">
                <Link to="/" className="crops-back">
                    ← Back to map
                </Link>
                <h1>Select crops</h1>
                <span
                    className="crops-info"
                    tabIndex={0}
                    title={
                        "Each crop's soil health score (0–100) sums how it " +
                        "treats the soil: nitrogen, organic matter, ground " +
                        "cover and break-crop value add to it; disturbance " +
                        "and harvest removal take away. Hover a row on a " +
                        "card for details."
                    }
                >
                    How scores work
                </span>
                <p className="crops-area">
                    Area: {describeSelection(selection)}
                </p>
            </header>

            {error && (
                <div className="crops-error">
                    {error}{" "}
                    <button onClick={() => setReloadKey((k) => k + 1)}>
                        Retry
                    </button>
                </div>
            )}
            {!error && !crops && <p>Loading available crops…</p>}
            {crops && crops.length === 0 && (
                <p>No crops are available for this area.</p>
            )}
            {crops && crops.length > 0 && (
                <>
                    <div className="crops-toolbar">
                        <input
                            type="search"
                            className="crops-search"
                            placeholder={`Search ${crops.length} crops by name, family or note…`}
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
                                <option value="score">Soil score, best first</option>
                                <option value="name">Name, A–Z</option>
                            </select>
                        </label>
                        <span className="crops-results">
                            {filtered.length === crops.length
                                ? `${crops.length} crops`
                                : `${filtered.length} of ${crops.length} match`}
                        </span>
                        <span className="crops-arrows">
                            <button
                                type="button"
                                aria-label="Scroll left"
                                disabled={scroller.atStart}
                                onClick={() => scrollPage(-1)}
                            >
                                ‹
                            </button>
                            <button
                                type="button"
                                aria-label="Scroll right"
                                disabled={scroller.atEnd}
                                onClick={() => scrollPage(1)}
                            >
                                ›
                            </button>
                        </span>
                    </div>

                    {selectedCrops.length > 0 && (
                        <div className="crops-selected">
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
                    )}

                    {filtered.length === 0 && (
                        <p className="crops-empty">
                            No crops match “{deferredQuery}”.
                        </p>
                    )}

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
                    <div className="crops-footer">
                        <p className="crops-count">
                            {selectedCrops.length} of {crops.length} selected
                        </p>
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
                </>
            )}
        </div>
    )
}

export default CropsPage
