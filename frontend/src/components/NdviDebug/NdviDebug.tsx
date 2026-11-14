import "./NdviDebug.scss"
import { useEffect, useRef, useState } from "react"
import { Field } from "../../api/fetchField"
import {
    FieldNdvi,
    NdviPeriod,
    NDVI_DEFAULT_FROM_YEAR,
    NDVI_DEFAULT_TO_YEAR,
    PeriodType,
    fetchFieldNdvi,
} from "../../api/fetchFieldNdvi"

// Debug panel: send the selected field rings to POST /api/fields/ndvi and
// show what comes back, as a timeline per field, a period table and raw JSON.

const FIRST_YEAR = 2017 // Sentinel-2 L2A
const THIS_YEAR = new Date().getFullYear()
const DAY_MS = 86_400_000

export const PERIOD_COLORS: Record<PeriodType, string> = {
    crop: "#16a34a",
    dormant_crop: "#86efac",
    green_cover: "#a3e635",
    barren: "#a16207",
    uncertain: "#9ca3af",
}

const PERIOD_LABELS: Record<PeriodType, string> = {
    crop: "crop",
    dormant_crop: "dormant crop",
    green_cover: "green cover",
    barren: "barren",
    uncertain: "uncertain",
}

const day = (iso: string) => Date.parse(`${iso}T00:00:00Z`) / DAY_MS

function periodTitle(p: NdviPeriod): string {
    const days = day(p.end) - day(p.start) + 1
    return [
        `${PERIOD_LABELS[p.type]}${p.crop_type ? ` (${p.crop_type})` : ""}`,
        `${p.start} → ${p.end} (${days} d)`,
        `confidence: ${p.confidence}`,
    ].join("\n")
}

type Result = {
    fields: Field[]
    response: FieldNdvi[]
    ms: number
}

function Timeline({
    periods,
    axisStart,
    axisEnd,
}: {
    periods: NdviPeriod[]
    axisStart: number
    axisEnd: number
}) {
    const span = axisEnd - axisStart
    return (
        <div className="ndvi-track">
            {periods.map((p) => {
                const left = ((day(p.start) - axisStart) / span) * 100
                const width = ((day(p.end) + 1 - day(p.start)) / span) * 100
                return (
                    <div
                        key={p.start}
                        className={`ndvi-segment confidence-${p.confidence}`}
                        style={{
                            left: `${left}%`,
                            width: `${width}%`,
                            backgroundColor: PERIOD_COLORS[p.type],
                        }}
                        title={periodTitle(p)}
                    />
                )
            })}
        </div>
    )
}

function YearAxis({ axisStart, axisEnd }: { axisStart: number; axisEnd: number }) {
    const span = axisEnd - axisStart
    const first = new Date(axisStart * DAY_MS).getUTCFullYear()
    const last = new Date(axisEnd * DAY_MS).getUTCFullYear()
    const years = []
    for (let y = first; y <= last; y++) {
        const at = (day(`${y}-01-01`) - axisStart) / span
        if (at >= 0 && at <= 1) years.push({ y, at })
    }
    return (
        <div className="ndvi-axis">
            {years.map(({ y, at }) => (
                <span key={y} style={{ left: `${at * 100}%` }}>
                    {y}
                </span>
            ))}
        </div>
    )
}

function NdviDebug({
    fields,
    fieldLabel,
    onClose,
}: {
    fields: Field[]
    fieldLabel: (field: Field) => string
    onClose: () => void
}) {
    const [fromYear, setFromYear] = useState(NDVI_DEFAULT_FROM_YEAR)
    const [toYear, setToYear] = useState(NDVI_DEFAULT_TO_YEAR)
    const [loading, setLoading] = useState(false)
    const [elapsed, setElapsed] = useState(0)
    const [error, setError] = useState<string | null>(null)
    const [result, setResult] = useState<Result | null>(null)
    const [openTable, setOpenTable] = useState<string | null>(null)
    const abortRef = useRef<AbortController | null>(null)

    useEffect(() => () => abortRef.current?.abort(), [])

    useEffect(() => {
        if (!loading) return
        const started = Date.now()
        const timer = setInterval(() => setElapsed(Date.now() - started), 250)
        return () => clearInterval(timer)
    }, [loading])

    const run = () => {
        abortRef.current?.abort()
        const controller = new AbortController()
        abortRef.current = controller
        const sent = fields
        const started = Date.now()
        setLoading(true)
        setError(null)
        fetchFieldNdvi(sent, fromYear, toYear, controller.signal)
            .then((response) =>
                setResult({ fields: sent, response, ms: Date.now() - started }),
            )
            .catch((err) => {
                if (controller.signal.aborted) return
                setError(err instanceof Error ? err.message : String(err))
            })
            .finally(() => {
                if (abortRef.current === controller) setLoading(false)
            })
    }

    const stale =
        result !== null &&
        (result.fields.length !== fields.length ||
            result.fields.some((f, i) => f.id !== fields[i]?.id))

    const allPeriods = result?.response.flatMap((r) => r.periods) ?? []
    const axisStart = allPeriods.length
        ? Math.min(...allPeriods.map((p) => day(p.start)))
        : 0
    const axisEnd = allPeriods.length
        ? Math.max(...allPeriods.map((p) => day(p.end) + 1))
        : 1

    const yearOptions = []
    for (let y = FIRST_YEAR; y <= THIS_YEAR; y++) yearOptions.push(y)

    return (
        <div className="ndvi-debug">
            <div className="ndvi-header">
                <strong>NDVI debug</strong>
                <span className="ndvi-muted">
                    POST /api/fields/ndvi · {fields.length} field
                    {fields.length === 1 ? "" : "s"}
                </span>
                <label>
                    from
                    <select
                        value={fromYear}
                        onChange={(e) => setFromYear(Number(e.target.value))}
                    >
                        {yearOptions.map((y) => (
                            <option key={y}>{y}</option>
                        ))}
                    </select>
                </label>
                <label>
                    to
                    <select
                        value={toYear}
                        onChange={(e) => setToYear(Number(e.target.value))}
                    >
                        {yearOptions.map((y) => (
                            <option key={y}>{y}</option>
                        ))}
                    </select>
                </label>
                <button
                    type="button"
                    className="ndvi-run"
                    disabled={loading || fields.length === 0 || fromYear > toYear}
                    onClick={run}
                >
                    {loading ? `Running… ${(elapsed / 1000).toFixed(0)} s` : "Run"}
                </button>
                {result && !loading && (
                    <span className="ndvi-muted">
                        took {(result.ms / 1000).toFixed(1)} s
                    </span>
                )}
                {stale && (
                    <span className="ndvi-warn">selection changed, run again</span>
                )}
                <button type="button" className="panel-toggle" onClick={onClose}>
                    Close
                </button>
            </div>

            <div className="ndvi-legend">
                {(Object.keys(PERIOD_COLORS) as PeriodType[]).map((t) => (
                    <span key={t}>
                        <i style={{ backgroundColor: PERIOD_COLORS[t] }} />
                        {PERIOD_LABELS[t]}
                    </span>
                ))}
                <span className="ndvi-muted">faded = lower confidence</span>
            </div>

            {error && <div className="ndvi-error">{error}</div>}
            {!result && !error && !loading && (
                <div className="ndvi-muted">
                    Press Run. Fields start loading in the background as soon
                    as they are picked (default years), so this is often
                    instant; other years are fetched on Run and then cached.
                </div>
            )}

            {result && (
                <div className="ndvi-rows">
                    {allPeriods.length > 0 && (
                        <div className="ndvi-row">
                            <span />
                            <YearAxis axisStart={axisStart} axisEnd={axisEnd} />
                        </div>
                    )}
                    {result.response.map((r, i) => {
                        const field = result.fields[i]
                        const key = r.id ?? String(i)
                        return (
                            <div key={key}>
                                <div className="ndvi-row">
                                    <button
                                        type="button"
                                        className="field-name"
                                        title={`${field ? fieldLabel(field) : key} (${key})`}
                                        onClick={() =>
                                            setOpenTable((o) => (o === key ? null : key))
                                        }
                                    >
                                        {openTable === key ? "▾ " : "▸ "}
                                        {field ? fieldLabel(field) : key}
                                    </button>
                                    {r.error ? (
                                        <span className="ndvi-error">{r.error}</span>
                                    ) : (
                                        <Timeline
                                            periods={r.periods}
                                            axisStart={axisStart}
                                            axisEnd={axisEnd}
                                        />
                                    )}
                                </div>
                                {openTable === key && r.periods.length > 0 && (
                                    <table className="ndvi-table">
                                        <thead>
                                            <tr>
                                                <th>start</th>
                                                <th>end</th>
                                                <th>days</th>
                                                <th>type</th>
                                                <th>crop type</th>
                                                <th>confidence</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {r.periods.map((p) => (
                                                <tr key={p.start}>
                                                    <td>{p.start}</td>
                                                    <td>{p.end}</td>
                                                    <td>{day(p.end) - day(p.start) + 1}</td>
                                                    <td>
                                                        <i
                                                            className="ndvi-swatch"
                                                            style={{
                                                                backgroundColor:
                                                                    PERIOD_COLORS[p.type],
                                                            }}
                                                        />
                                                        {PERIOD_LABELS[p.type]}
                                                    </td>
                                                    <td>{p.crop_type ?? "–"}</td>
                                                    <td>{p.confidence}</td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                )}
                            </div>
                        )
                    })}
                    <details className="ndvi-raw">
                        <summary>Raw response</summary>
                        <pre>{JSON.stringify(result.response, null, 2)}</pre>
                    </details>
                </div>
            )}
        </div>
    )
}

export default NdviDebug
