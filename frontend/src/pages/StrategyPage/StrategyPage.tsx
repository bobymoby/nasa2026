import "./StrategyPage.scss"
import { useEffect, useState } from "react"
import { Link } from "react-router"

import { fetchRotationRisks } from "../../api/fetchRotationRisks"
import {
    Assessment,
    fetchStrategies,
    Rating,
    Strategy,
} from "../../api/fetchStrategies"
import { AreaSelection, describeSelection } from "../../data/point"
import EconomicsSection, { formatSigned } from "./EconomicsSection"
import RotationTimeline from "./RotationTimeline"
import WaterChart from "./WaterChart"
import { humanize, LEVEL_LABELS } from "./labels"
import RiskBadge from "./RiskBadge"
import {
    plantingSequence,
    RiskState,
    RiskSummary,
    RotationRisksSection,
} from "./RotationRisks"

type Props = {
    selection: AreaSelection
    crops: string[]
}

const RATINGS: { key: keyof Strategy["ratings"]; label: string }[] = [
    { key: "nutrientCycling", label: "Nutrient cycling" },
    { key: "organicMatter", label: "Organic matter" },
    { key: "pestDisruption", label: "Pest-cycle disruption" },
    { key: "soilImprovement", label: "Soil improvement" },
    { key: "foragePotential", label: "Forage potential" },
    { key: "market", label: "Market" },
]

const CROP_ROLES: {
    key: keyof Strategy["cropRoles"]
    label: string
    icon: string
}[] = [
    { key: "nitrogenFixing", label: "Nitrogen-fixing crops", icon: "N" },
    { key: "coverCrops", label: "Cover crops", icon: "◐" },
    { key: "deepRooting", label: "Deep-rooting crops", icon: "↓" },
    { key: "droughtTolerant", label: "Drought-tolerant crops", icon: "☀" },
]

function ScoreMeter({ score }: { score: number }) {
    return (
        <span className="score-meter" aria-label={`${score} out of 5`}>
            {[1, 2, 3, 4, 5].map((n) => (
                <i key={n} className={n <= score ? "filled" : undefined} />
            ))}
        </span>
    )
}

function RatingCard({ label, rating }: { label: string; rating: Rating }) {
    return (
        <div className="rating-card">
            <div className="rating-head">
                <span>{label}</span>
                <span className="rating-score">
                    <ScoreMeter score={rating.score} />
                    {rating.score}/5
                </span>
            </div>
            <p>{rating.summary}</p>
        </div>
    )
}

function AssessmentCard({
    label,
    assessment,
}: {
    label: string
    assessment: Assessment
}) {
    return (
        <div className="rating-card">
            <div className="rating-head">
                <span>{label}</span>
                <RiskBadge level={assessment.level} label="" />
            </div>
            <p>{assessment.summary}</p>
        </div>
    )
}

function averageScore(s: Strategy): number {
    const scores = Object.values(s.ratings).map((r) => r.score)
    return scores.reduce((a, b) => a + b, 0) / scores.length
}

function StrategyDetail({
    strategy,
    risk,
}: {
    strategy: Strategy
    risk: RiskState | undefined
}) {
    return (
        <article className="strategy-detail" key={strategy.id}>
            <header>
                <h2>{strategy.name}</h2>
                <p>{strategy.summary}</p>
                <div className="strategy-tags">
                    <span className="tag">
                        {strategy.durationYears}-year plan
                    </span>
                    <span className="tag tag-money">
                        {formatSigned(strategy.economics.profitPerHaYear)}/ha/yr
                        profit
                    </span>
                    <RiskBadge
                        level={strategy.erosionRisk.level}
                        label="erosion risk"
                    />
                    <RiskBadge
                        level={strategy.waterUse.level}
                        label="water use"
                    />
                    <RiskSummary state={risk} />
                </div>
            </header>

            <section>
                <h3>What to plant and when</h3>
                <RotationTimeline
                    plantings={strategy.plantings}
                    years={strategy.durationYears}
                />
            </section>

            <section>
                <h3>Rotation risks</h3>
                <RotationRisksSection state={risk} plantings={strategy.plantings} />
            </section>

            <section>
                <h3>Estimated cost and profit</h3>
                <EconomicsSection
                    economics={strategy.economics}
                    years={strategy.durationYears}
                />
            </section>

            <section>
                <h3>Assessment</h3>
                <div className="rating-grid">
                    {RATINGS.map(({ key, label }) => (
                        <RatingCard
                            key={key}
                            label={label}
                            rating={strategy.ratings[key]}
                        />
                    ))}
                    <AssessmentCard
                        label="Erosion risk"
                        assessment={strategy.erosionRisk}
                    />
                    <AssessmentCard
                        label="Water use"
                        assessment={strategy.waterUse}
                    />
                </div>
            </section>

            <section>
                <h3>Water consumption per year</h3>
                <WaterChart
                    byYearMm={strategy.waterUse.byYearMm}
                    plantings={strategy.plantings}
                />
            </section>

            <section>
                <h3>Role of each crop type</h3>
                <div className="role-grid">
                    {CROP_ROLES.map(({ key, label, icon }) => {
                        const role = strategy.cropRoles[key]
                        return (
                            <div className="role-card" key={key}>
                                <div className="role-head">
                                    <span
                                        className="role-icon"
                                        aria-hidden="true"
                                    >
                                        {icon}
                                    </span>
                                    <span className="role-name">{label}</span>
                                    <span
                                        className={`importance importance-${role.level}`}
                                    >
                                        {LEVEL_LABELS[role.level]} importance
                                    </span>
                                </div>
                                <p>{role.summary}</p>
                            </div>
                        )
                    })}
                </div>
            </section>

            <section>
                <h3>Market considerations</h3>
                <ul className="market-list">
                    {strategy.marketConsiderations.map((m) => (
                        <li key={m}>{m}</li>
                    ))}
                </ul>
            </section>
        </article>
    )
}

function ComparisonTable({
    strategies,
    risks,
    selectedId,
    onSelect,
}: {
    strategies: Strategy[]
    risks: Record<string, RiskState>
    selectedId: string
    onSelect: (id: string) => void
}) {
    return (
        <div className="comparison">
            <table>
                <thead>
                    <tr>
                        <th>Strategy</th>
                        <th>Years</th>
                        <th>Profit €/ha/yr</th>
                        {RATINGS.map(({ key, label }) => (
                            <th key={key}>{label}</th>
                        ))}
                        <th>Erosion risk</th>
                        <th>Water use</th>
                        <th>Rotation risks</th>
                    </tr>
                </thead>
                <tbody>
                    {strategies.map((s) => (
                        <tr
                            key={s.id}
                            className={
                                s.id === selectedId ? "selected" : undefined
                            }
                            onClick={() => onSelect(s.id)}
                        >
                            <th scope="row">{s.name}</th>
                            <td>{s.durationYears}</td>
                            <td
                                className={`cell-money${s.economics.profitPerHaYear < 0 ? " negative" : ""}`}
                            >
                                {formatSigned(s.economics.profitPerHaYear)}
                            </td>
                            {RATINGS.map(({ key }) => (
                                <td key={key}>
                                    <span className="cell-score">
                                        {s.ratings[key].score}/5
                                    </span>
                                </td>
                            ))}
                            <td>
                                <RiskBadge
                                    level={s.erosionRisk.level}
                                    label=""
                                />
                            </td>
                            <td>
                                <RiskBadge level={s.waterUse.level} label="" />
                            </td>
                            <td>
                                <RiskSummary state={risks[s.id]} />
                            </td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    )
}

function StrategyPage({ selection, crops }: Props) {
    const [strategies, setStrategies] = useState<Strategy[] | null>(null)
    const [error, setError] = useState<string | null>(null)
    const [reloadKey, setReloadKey] = useState(0)
    const [selectedId, setSelectedId] = useState<string | null>(null)
    const [risks, setRisks] = useState<Record<string, RiskState>>({})

    useEffect(() => {
        const controller = new AbortController()
        setStrategies(null)
        setError(null)

        fetchStrategies(selection, crops, controller.signal)
            .then((result) => {
                setStrategies(result)
                setSelectedId(result[0]?.id ?? null)
            })
            .catch((err) => {
                if (controller.signal.aborted) return
                setError(
                    err instanceof Error
                        ? err.message
                        : "Failed to load strategies",
                )
            })

        return () => controller.abort()
    }, [selection, crops, reloadKey])

    // check every strategy's planting order once; strategies with the same
    // order share one request
    useEffect(() => {
        if (!strategies) return
        const controller = new AbortController()
        const bySequence = new Map<string, string[]>()
        for (const s of strategies) {
            const key = plantingSequence(s.plantings).join(",")
            bySequence.set(key, [...(bySequence.get(key) ?? []), s.id])
        }
        setRisks(
            Object.fromEntries(
                strategies.map((s) => [s.id, { status: "loading" } as RiskState]),
            ),
        )
        bySequence.forEach((ids, key) => {
            const settle = (state: RiskState) =>
                setRisks((prev) => ({
                    ...prev,
                    ...Object.fromEntries(ids.map((id) => [id, state])),
                }))
            if (!key) {
                settle({ status: "ok", issues: [] })
                return
            }
            fetchRotationRisks(key.split(","), controller.signal)
                .then((issues) => settle({ status: "ok", issues }))
                .catch((err) => {
                    if (controller.signal.aborted) return
                    settle({
                        status: "error",
                        message:
                            err instanceof Error
                                ? err.message
                                : "Failed to check rotation risks",
                    })
                })
        })
        return () => controller.abort()
    }, [strategies])

    const selected = strategies?.find((s) => s.id === selectedId)

    return (
        <div className="strategy-page">
            <Link to="/crops" className="strategy-back">
                ← Back to crops
            </Link>
            <h1 className="strategy-title">Rotation strategies</h1>
            <p className="strategy-lead">
                Multi-year plans for {describeSelection(selection)} using
            </p>
            <div className="crop-chips">
                {crops.map((c) => (
                    <span key={c} className="crop-chip">
                        {humanize(c)}
                    </span>
                ))}
            </div>

            {error && (
                <div className="strategy-error">
                    {error}
                    <button onClick={() => setReloadKey((k) => k + 1)}>
                        Retry
                    </button>
                </div>
            )}

            {!error && !strategies && (
                <div className="strategy-loading" aria-busy="true">
                    <div />
                    <div />
                    <div />
                </div>
            )}

            {strategies && strategies.length === 0 && (
                <p>No strategies fit these crops yet.</p>
            )}

            {strategies && strategies.length > 0 && (
                <>
                    <div className="strategy-picker" role="tablist">
                        {strategies.map((s) => (
                            <button
                                key={s.id}
                                type="button"
                                role="tab"
                                aria-selected={s.id === selectedId}
                                className={`strategy-option${s.id === selectedId ? " selected" : ""}`}
                                onClick={() => setSelectedId(s.id)}
                            >
                                <span className="option-name">{s.name}</span>
                                <span className="option-meta">
                                    {s.durationYears} years · avg{" "}
                                    {averageScore(s).toFixed(1)}/5
                                </span>
                                <span
                                    className={`option-profit${s.economics.profitPerHaYear < 0 ? " negative" : ""}`}
                                >
                                    {formatSigned(s.economics.profitPerHaYear)}
                                    <small> /ha/yr est. profit</small>
                                </span>
                                <span className="option-risk">
                                    <RiskSummary state={risks[s.id]} />
                                </span>
                                <span
                                    className={`option-match${s.matchedCrops.length ? "" : " none"}`}
                                >
                                    {s.matchedCrops.length
                                        ? `Uses ${s.matchedCrops.map(humanize).join(", ")}`
                                        : "Uses none of your crops"}
                                </span>
                            </button>
                        ))}
                    </div>

                    {selected && (
                        <StrategyDetail strategy={selected} risk={risks[selected.id]} />
                    )}

                    <section className="comparison-section">
                        <h3>Compare all strategies</h3>
                        <ComparisonTable
                            strategies={strategies}
                            risks={risks}
                            selectedId={selectedId ?? ""}
                            onSelect={setSelectedId}
                        />
                    </section>
                </>
            )}
        </div>
    )
}

export default StrategyPage
