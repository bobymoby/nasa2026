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

const countryNames = new Intl.DisplayNames(["en"], { type: "region" })

function countryName(code: string): string {
    try {
        return countryNames.of(code) ?? code
    } catch {
        return code
    }
}

function formatYield(t: number | null): string {
    return t == null
        ? "—"
        : `${t.toLocaleString("en-IE", { maximumFractionDigits: 2 })} t`
}

function formatPrice(eurPerT: number | null): string {
    return eurPerT == null ? "—" : `${formatEur(eurPerT)}/t`
}

function formatHa(ha: number): string {
    return `${ha.toLocaleString("en-IE", { maximumFractionDigits: ha < 10 ? 2 : 0 })} ha`
}

function RevenueChart({ byYear }: { byYear: Economics["byYear"] }) {
    const [hovered, setHovered] = useState<number | null>(null)
    const max = Math.max(...byYear.map((y) => y.revenue)) || 1

    return (
        <div className="revenue-chart">
            <div className="revenue-plot">
                {byYear.map((y, i) => {
                    const height = (y.revenue / max) * 100
                    return (
                        <div
                            key={y.year}
                            className={`revenue-col${hovered === i ? " hovered" : ""}`}
                            onMouseEnter={() => setHovered(i)}
                            onMouseLeave={() => setHovered(null)}
                        >
                            <div
                                className="revenue-bar"
                                style={{ height: `${height}%` }}
                            >
                                <span className="revenue-value">
                                    {formatEur(y.revenue)}
                                </span>
                            </div>
                            {hovered === i && (
                                <div className="chart-tooltip revenue-tooltip">
                                    <strong>Year {y.year}</strong>
                                    <div>Revenue {formatEur(y.revenue)}</div>
                                </div>
                            )}
                        </div>
                    )
                })}
            </div>
            <div className="revenue-labels">
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
    const country = countryName(economics.country)

    return (
        <div className="economics">
            <div className="stat-tiles">
                <div className="stat-tile hero">
                    <span className="stat-label">
                        Revenue per hectare per year
                    </span>
                    <span className="stat-value">
                        {formatEur(economics.revenuePerHaYear)}
                    </span>
                </div>
                <div className="stat-tile">
                    <span className="stat-label">
                        With EU CAP payments, per ha per year
                    </span>
                    <span className="stat-value">
                        {formatEur(economics.revenueWithCapPerHaYear)}
                    </span>
                </div>
                <div className="stat-tile">
                    <span className="stat-label">
                        Revenue on {formatHa(economics.areaHa)} over {years}{" "}
                        years
                    </span>
                    <span className="stat-value">
                        {formatEur(economics.totalRevenue)}
                    </span>
                </div>
                <div className="stat-tile">
                    <span className="stat-label">
                        Revenue per ha, whole rotation
                    </span>
                    <span className="stat-value">
                        {formatEur(economics.revenuePerHa)}
                    </span>
                </div>
            </div>

            <h4>Revenue by harvest year (per ha)</h4>
            <RevenueChart byYear={economics.byYear} />

            <h4>By crop (per ha)</h4>
            <div className="economics-table">
                <table>
                    <thead>
                        <tr>
                            <th>Crop</th>
                            <th>Role</th>
                            <th className="num">Yield /ha</th>
                            <th className="num">Price</th>
                            <th className="num">Revenue</th>
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
                                <td className="num">
                                    {formatYield(p.yieldTHa)}
                                </td>
                                <td className="num">
                                    {formatPrice(p.priceEurT)}
                                </td>
                                <td className="num">{formatEur(p.revenue)}</td>
                                <td className="basis">{p.source}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>

            <h4>EU CAP payments (per ha, whole rotation)</h4>
            {economics.capPayments.length > 0 ? (
                <div className="economics-table">
                    <table>
                        <thead>
                            <tr>
                                <th>Payment</th>
                                <th className="num">Amount</th>
                            </tr>
                        </thead>
                        <tbody>
                            {economics.capPayments.map((p) => (
                                <tr key={p.name}>
                                    <td>{p.name}</td>
                                    <td className="num">
                                        {formatEur(p.eurPerHa)}
                                    </td>
                                </tr>
                            ))}
                            <tr className="cap-total">
                                <td>Total</td>
                                <td className="num">
                                    {formatEur(economics.capPerHa)}
                                </td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            ) : (
                <p className="economics-note">
                    No CAP payments apply to this plan.
                </p>
            )}

            <p className="economics-note">
                Revenue uses {country}'s national five-year average yield and
                farm-gate price (Eurostat, FAOSTAT where Eurostat has no data),
                with prices in {economics.priceYear ?? "the latest available"}{" "}
                euros. There is no sourced cost data, so costs and profit are
                not shown. CAP payments are the amounts Bulgaria paid per
                hectare for claim year 2024 (Ministry of Agriculture, Annual
                Performance Report 2025) and assume you apply and meet each
                scheme’s conditions; the selected area is treated as your whole
                farm. Money is counted in the year the crop is harvested.
            </p>
        </div>
    )
}

export default EconomicsSection
