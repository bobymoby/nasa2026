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
import { fetchPlannableCrops } from "../../api/fetchStrategies"
import { AreaSelection, describeSelection } from "../../data/point"
import CropCard, { CardDrag, DropSide } from "./CropCard"
import ScoreInfo from "./ScoreInfo"
import { ListedCrop, listCrops } from "./listedCrops"
import { useSmoothHorizontalScroll } from "./useSmoothHorizontalScroll"

// smallest height a card fits in; keep in sync with the card styles
const MIN_CARD_HEIGHT = 250
const GRID_GAP = 12

type SortKey = "suitability" | "soil" | "name"

const SORTERS: Record<SortKey, (a: ListedCrop, b: ListedCrop) => number> = {
    suitability: (a, b) =>
        b.gaez.suitability - a.gaez.suitability ||
        (b.soil?.score ?? -1) - (a.soil?.score ?? -1),
    soil: (a, b) => (b.soil?.score ?? -1) - (a.soil?.score ?? -1),
    name: (a, b) => a.name.localeCompare(b.name),
}

type PageLink = { to: string; label: string }

type Props = {
    selection: AreaSelection
    selectedCrops: string[]
    onSelectedCropsChange: (ids: string[]) => void
    title?: string
    intro?: string
    back?: PageLink
    // emptyLabel lets the user continue without picking anything
    next: PageLink & { emptyLabel?: string }
    // limits the cards to these crop ids
    onlyCrops?: string[]
    // the selection is a planting sequence: cards show their position and the
    // footer can reorder it
    ordered?: boolean
}

function CropsPage({
    selection,
    selectedCrops,
    onSelectedCropsChange,
    title = "Select crops",
    intro,
    back = { to: "/", label: "← Back to map" },
    next,
    onlyCrops,
    ordered = false,
}: Props) {
    const [gaez, setGaez] = useState<CropsResult | null>(null)
    const [gaezError, setGaezError] = useState<string | null>(null)
    const [soil, setSoil] = useState<SoilScoredCrop[] | null>(null)
    const [soilError, setSoilError] = useState<string | null>(null)
    const [plannable, setPlannable] = useState<Set<string> | null>(null)
    const [plannableError, setPlannableError] = useState<string | null>(null)
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

    // only crops the strategy planner has complete data for
    useEffect(() => {
        const controller = new AbortController()
        setPlannable(null)
        setPlannableError(null)
        fetchPlannableCrops(selection, controller.signal)
            .then((ids) => setPlannable(new Set(ids)))
            .catch((err) => {
                if (controller.signal.aborted) return
                setPlannableError(
                    err instanceof Error ? err.message : String(err),
                )
            })
        return () => controller.abort()
    }, [selection, reloadKey])

    const all = useMemo(
        () =>
            listCrops(gaez?.crops ?? null, soil).filter(
                (c) =>
                    plannable?.has(c.id) &&
                    (!onlyCrops || onlyCrops.includes(c.id)),
            ),
        [gaez, soil, plannable, onlyCrops],
    )

    // Drop picked crops not offered for this area after suitability loads.
    useEffect(() => {
        if (!gaez || !plannable) return
        const available = new Set(all.map((c) => c.id))
        const kept = selectedCrops.filter((id) => available.has(id))
        if (kept.length !== selectedCrops.length) onSelectedCropsChange(kept)
    }, [all, gaez, plannable, selectedCrops, onSelectedCropsChange])

    const shown = useMemo(
        () => all.filter((c) => c.gaez.suitability >= MIN_SUITABILITY),
        [all],
    )

    const filtered = useMemo(() => {
        const words = deferredQuery.toLowerCase().split(/\s+/).filter(Boolean)
        return shown
            .filter((c) => words.every((w) => c.searchText.includes(w)))
            .sort(SORTERS[sort])
    }, [shown, deferredQuery, sort])

    const selectedSet = useMemo(() => new Set(selectedCrops), [selectedCrops])

    // the planned rotation lists its sequence first, in planting order
    const displayed = useMemo(() => {
        if (!ordered) return filtered
        const byCrop = new Map(filtered.map((c) => [c.id, c]))
        const picked = selectedCrops.flatMap((id) => byCrop.get(id) ?? [])
        return [...picked, ...filtered.filter((c) => !selectedSet.has(c.id))]
    }, [ordered, filtered, selectedCrops, selectedSet])

    const ready = gaez !== null && plannable !== null

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
    const move = useCallback((id: string, by: -1 | 1) => {
        const next = [...selectedRef.current]
        const from = next.indexOf(id)
        const to = from + by
        if (from < 0 || to < 0 || to >= next.length) return
        ;[next[from], next[to]] = [next[to], next[from]]
        onChangeRef.current(next)
    }, [])
    const [dragged, setDragged] = useState<string | null>(null)
    const [dropTarget, setDropTarget] = useState<{
        id: string
        side: DropSide
    } | null>(null)
    const dropTargetRef = useRef(dropTarget)
    dropTargetRef.current = dropTarget
    const draggedRef = useRef(dragged)
    draggedRef.current = dragged
    // a dragged crop lands before or after the card it is dropped on; dropping
    // on an unpicked crop sends it to the end of the sequence
    const drag = useMemo<CardDrag>(
        () => ({
            start: (id) => setDragged(id),
            over: (id, side) =>
                setDropTarget((prev) =>
                    prev?.id === id && prev.side === side ? prev : { id, side },
                ),
            drop: () => {
                const id = draggedRef.current
                const target = dropTargetRef.current
                setDragged(null)
                setDropTarget(null)
                if (!id || !target || target.id === id) return
                const rest = selectedRef.current.filter((c) => c !== id)
                const at = rest.indexOf(target.id)
                if (at < 0) {
                    if (selectedRef.current.includes(id))
                        onChangeRef.current([...rest, id])
                    return
                }
                rest.splice(target.side === "after" ? at + 1 : at, 0, id)
                onChangeRef.current(rest)
            },
            end: () => {
                setDragged(null)
                setDropTarget(null)
            },
        }),
        [],
    )
    const position = useMemo(
        () => new Map(selectedCrops.map((id, i) => [id, i + 1])),
        [selectedCrops],
    )

    const errors = [gaezError, soilError, plannableError].filter(Boolean)
    const cells = gaez
        ? gaez.cells === 1
            ? "one ~9 km grid cell"
            : `averaged over ${gaez.cells} ~9 km grid cells`
        : "~9 km grid cells"

    return (
        <div className="crops-page">
            <header className="crops-header">
                <Link to={back.to} className="crops-back">
                    {back.label}
                </Link>
                <h1>{title}</h1>
                <ScoreInfo cells={cells} />
                <p className="crops-area">
                    Area: {describeSelection(selection)}
                </p>
                {intro && <p className="crops-intro">{intro}</p>}
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
                        className={`crops-list${ordered ? " crops-list-ordered" : ""}`}
                        style={
                            ordered
                                ? undefined
                                : {
                                      gridTemplateRows: `repeat(${rows}, minmax(${MIN_CARD_HEIGHT}px, 1fr))`,
                                  }
                        }
                    >
                        {displayed.map((crop) => (
                            <CropCard
                                key={crop.id}
                                crop={crop}
                                checked={selectedSet.has(crop.id)}
                                order={
                                    ordered
                                        ? (position.get(crop.id) ?? 0)
                                        : undefined
                                }
                                onToggle={toggle}
                                drag={ordered ? drag : undefined}
                                dragging={dragged === crop.id}
                                dropSide={
                                    dragged &&
                                    dropTarget?.id === crop.id &&
                                    dropTarget.id !== dragged
                                        ? dropTarget.side
                                        : undefined
                                }
                            />
                        ))}
                    </ul>
                </div>
            ) : (
                <div className="crops-scroller-placeholder" />
            )}

            <div className="crops-footer">
                {ordered ? (
                    <ol className="crops-selected crops-sequence">
                        <li className="crops-count">
                            {selectedCrops.length > 0
                                ? "Planting order"
                                : "Click crops in the order you plant them"}
                        </li>
                        {selectedCrops.map((id, i) => {
                            const name = byId.get(id)?.name ?? id
                            return (
                                <li key={id} className="crops-step">
                                    <span className="crops-step-chip">
                                        <span className="crops-step-number">
                                            {i + 1}
                                        </span>
                                        {name}
                                        <button
                                            type="button"
                                            disabled={i === 0}
                                            onClick={() => move(id, -1)}
                                            aria-label={`Plant ${name} earlier`}
                                        >
                                            ‹
                                        </button>
                                        <button
                                            type="button"
                                            disabled={
                                                i === selectedCrops.length - 1
                                            }
                                            onClick={() => move(id, 1)}
                                            aria-label={`Plant ${name} later`}
                                        >
                                            ›
                                        </button>
                                        <button
                                            type="button"
                                            onClick={() => toggle(id)}
                                            aria-label={`Remove ${name}`}
                                        >
                                            ×
                                        </button>
                                    </span>
                                </li>
                            )
                        })}
                    </ol>
                ) : (
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
                )}
                {selectedCrops.length > 0 || next.emptyLabel ? (
                    <Link to={next.to} className="crops-next">
                        {selectedCrops.length > 0
                            ? next.label
                            : next.emptyLabel}
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
