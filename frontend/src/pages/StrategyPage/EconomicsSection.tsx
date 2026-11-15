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

<<<<<<< HEAD
function formatYield(t: number): string {
    return `${t.toLocaleString("en-IE", { maximumFractionDigits: 2 })} t`
=======
function formatYield(t: number | null): string {
    return t == null
        ? "—"
        : `${t.toLocaleString("en-IE", { maximumFractionDigits: 2 })} t`
>>>>>>> origin/main
}

function formatPrice(eurPerT: number): string {
    return `${formatEur(eurPerT)}/t`
}

function formatHa(ha: number): string {
    return `${ha.toLocaleString("en-IE", { maximumFractionDigits: ha < 10 ? 2 : 0 })} ha`
}

function RevenueChart({ byYear }: { byYear: Economics["byYear"] }) {
    const [hovered, setHovered] = useState<number | null>(null)
    const max = Math.max(...byYear.map((y) => y.revenue)) || 1

    return (
        <div className="profit-chart">
            <div className="profit-plot">
                <div
                    className="profit-zero"
                    style={{ top: "100%" }}
                    aria-hidden="true"
                />
                {byYear.map((y, i) => {
                    const height = (y.revenue / max) * 100
                    return (
                        <div
                            key={y.year}
                            className={`profit-col${hovered === i ? " hovered" : ""}`}
                            onMouseEnter={() => setHovered(i)}
                            onMouseLeave={() => setHovered(null)}
                        >
                            <div
                                className="profit-bar"
                                style={{
                                    height: `${height}%`,
                                    top: `${100 - height}%`,
                                }}
                            >
                                <span className="profit-value">
                                    {formatEur(y.revenue)}
                                </span>
                            </div>
                            {hovered === i && (
                                <div className="chart-tooltip profit-tooltip">
                                    <strong>Year {y.year}</strong>
                                    <div>Revenue {formatEur(y.revenue)}</div>
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
<<<<<<< HEAD
                        Revenue on {formatHa(economics.areaHa)} over {years}{" "}
=======
                        With EU CAP payments, per ha per year
                    </span>
                    <span
                        className={`stat-value${economics.profitWithCapPerHaYear < 0 ? " negative" : ""}`}
                    >
                        {formatSigned(economics.profitWithCapPerHaYear)}
                    </span>
                </div>
                <div className="stat-tile">
                    <span className="stat-label">
                        Profit on {formatHa(economics.areaHa)} over {years}{" "}
>>>>>>> origin/main
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
<<<<<<< HEAD
                Revenue is {country}'s national five-year average yield times
                its farm-gate price (Eurostat, FAOSTAT where Eurostat has no
                data), with prices in {economics.priceYear} euros. Vegetable
                yields are fresh weight. There is no cost data, so costs and
                profit are not shown. Money is counted in the year the crop is
                harvested.
=======
                Revenue uses {country}'s national five-year average yield and
                farm-gate price (Eurostat, FAOSTAT where Eurostat has no data),
                with prices in {economics.priceYear ?? "the latest available"}{" "}
                euros. Seed and operating costs are estimates. CAP payments are
                the amounts Bulgaria paid per hectare for claim year 2024
                (Ministry of Agriculture, Annual Performance Report 2025) and
                assume you apply and meet each scheme’s conditions; the selected
                area is treated as your whole farm. Land rent, labour, overheads
                and tax are not included. Money is counted in the year the crop
                is harvested.
>>>>>>> origin/main
            </p>
        </div>
    )
}

export default EconomicsSection
