import { useState } from "react"

import { Economics } from "../../api/fetchStrategies"
import { ROLE_LABELS } from "./labels"

const eur = new Intl.NumberFormat("en-IE", {
    style: "currency",
    currency: "EUR",
    maximumFractionDigits: 0,
})

export function formatEur(value: number): string {
    return eur.format(value)
}

// "+€1,234" / "−€180", for profit figures that can go negative
export function formatSigned(value: number): string {
    return `${value < 0 ? "−" : "+"}${eur.format(Math.abs(value))}`
}

function formatHa(ha: number): string {
    return `${ha.toLocaleString("en-IE", { maximumFractionDigits: ha < 10 ? 2 : 0 })} ha`
}

// profit per rotation year, with bars above or below a zero line
function ProfitChart({ byYear }: { byYear: Economics["byYear"] }) {
    const [hovered, setHovered] = useState<number | null>(null)
    const max = Math.max(0, ...byYear.map((y) => y.profit))
    const min = Math.min(0, ...byYear.map((y) => y.profit))
    const span = max - min || 1
    // share of the plot height above the zero line
    const zeroAt = max / span

    return (
        <div className="profit-chart">
            <div className="profit-plot">
                <div
                    className="profit-zero"
                    style={{ top: `${zeroAt * 100}%` }}
                    aria-hidden="true"
                />
                {byYear.map((y, i) => {
                    const height = (Math.abs(y.profit) / span) * 100
                    const negative = y.profit < 0
                    return (
                        <div
                            key={y.year}
                            className={`profit-col${hovered === i ? " hovered" : ""}`}
                            onMouseEnter={() => setHovered(i)}
                            onMouseLeave={() => setHovered(null)}
                        >
                            <div
                                className={`profit-bar${negative ? " negative" : ""}`}
                                style={{
                                    height: `${height}%`,
                                    top: negative
                                        ? `${zeroAt * 100}%`
                                        : `${(zeroAt * 100) - height}%`,
                                }}
                            >
                                <span className="profit-value">
                                    {formatSigned(y.profit)}
                                </span>
                            </div>
                            {hovered === i && (
                                <div className="chart-tooltip profit-tooltip">
                                    <strong>Year {y.year}</strong>
                                    <div>Revenue {formatEur(y.revenue)}</div>
                                    <div>Costs {formatEur(y.cost)}</div>
                                    <div>Profit {formatSigned(y.profit)}</div>
                                </div>
                            )}
                        </div>
                    )
                })}
            </div>
            <div className="profit-labels">
                {byYear.map((y) => (
                    <span key={y.year}>Year {y.year}</span>
                ))}
            </div>
        </div>
    )
}

function EconomicsSection({
    economics,
    years,
}: {
    economics: Economics
    years: number
}) {
    const { perHa, total } = economics
    const breakdown = [
        { label: "Seed", value: perHa.seedCost },
        { label: "Fertiliser, crop protection, machinery", value: perHa.operatingCost },
    ]

    return (
        <div className="economics">
            <div className="stat-tiles">
                <div className="stat-tile hero">
                    <span className="stat-label">Profit per hectare per year</span>
                    <span className={`stat-value${economics.profitPerHaYear < 0 ? " negative" : ""}`}>
                        {formatSigned(economics.profitPerHaYear)}
                    </span>
                </div>
                <div className="stat-tile">
                    <span className="stat-label">
                        Profit on {formatHa(economics.areaHa)} over {years} years
                    </span>
                    <span className={`stat-value${total.profit < 0 ? " negative" : ""}`}>
                        {formatSigned(total.profit)}
                    </span>
                </div>
                <div className="stat-tile">
                    <span className="stat-label">Revenue per ha, whole rotation</span>
                    <span className="stat-value">{formatEur(perHa.revenue)}</span>
                </div>
                <div className="stat-tile">
                    <span className="stat-label">Costs per ha, whole rotation</span>
                    <span className="stat-value">{formatEur(perHa.totalCost)}</span>
                </div>
            </div>

            <div className="economics-columns">
                <div>
                    <h4>Profit by harvest year (per ha)</h4>
                    <ProfitChart byYear={economics.byYear} />
                </div>
                <div>
                    <h4>Where the money goes (per ha, whole rotation)</h4>
                    <dl className="cost-breakdown">
                        <div className="revenue">
                            <dt>Revenue</dt>
                            <dd>{formatEur(perHa.revenue)}</dd>
                        </div>
                        {breakdown.map((b) => (
                            <div key={b.label}>
                                <dt>{b.label}</dt>
                                <dd>−{formatEur(b.value)}</dd>
                            </div>
                        ))}
                        <div className="result">
                            <dt>Profit</dt>
                            <dd className={perHa.profit < 0 ? "negative" : undefined}>
                                {formatSigned(perHa.profit)}
                            </dd>
                        </div>
                    </dl>
                </div>
            </div>

            <h4>By crop (per ha)</h4>
            <div className="economics-table">
                <table>
                    <thead>
                        <tr>
                            <th>Crop</th>
                            <th>Role</th>
                            <th className="num">Revenue</th>
                            <th className="num">Cost</th>
                            <th className="num">Profit</th>
                            <th>Basis</th>
                        </tr>
                    </thead>
                    <tbody>
                        {economics.plantings.map((p, i) => (
                            <tr key={i}>
                                <td>
                                    <i className={`role-dot role-${p.role}`} />
                                    {p.cropName}
                                </td>
                                <td>{ROLE_LABELS[p.role]}</td>
                                <td className="num">{formatEur(p.revenue)}</td>
                                <td className="num">{formatEur(p.cost)}</td>
                                <td className={`num${p.profit < 0 ? " negative" : ""}`}>
                                    {formatSigned(p.profit)}
                                </td>
                                <td className="basis">{p.source}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>

            <p className="economics-note">
                Estimates.
                {economics.priceYear &&
                    ` Yields, prices and seed costs: Bulgarian market data for ${economics.priceYear}.`}{" "}
                Other costs, cover crops and alfalfa hay are typical figures.
                Land rent, subsidies, labour, overheads and tax are not
                included. Money is
                counted in the year the crop is harvested.
            </p>
        </div>
    )
}

export default EconomicsSection
