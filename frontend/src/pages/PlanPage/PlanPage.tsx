import "./PlanPage.scss"
import { DragEvent, useMemo, useState } from "react"
import { Link } from "react-router"

import { peekCrops } from "../../api/fetchCrops"
import { AreaSelection, describeSelection } from "../../data/point"
import { humanize } from "../StrategyPage/labels"

// the strategies endpoint accepts at most this many planned plantings
export const MAX_PLAN_LENGTH = 8

type Dragged = { from: "option"; id: string } | { from: "plan"; index: number }

const DRAG_TYPE = "application/x-planned-crop"

type Props = {
    selection: AreaSelection
    crops: string[]
    plan: string[]
    onPlanChange: (plan: string[]) => void
}

function readDragged(e: DragEvent): Dragged | null {
    try {
        return JSON.parse(e.dataTransfer.getData(DRAG_TYPE))
    } catch {
        return null
    }
}

// before or after the hovered chip, by which half the pointer is over
function sideOf(e: DragEvent<HTMLElement>, index: number): number {
    const box = e.currentTarget.getBoundingClientRect()
    return e.clientX < box.left + box.width / 2 ? index : index + 1
}

function PlanPage({ selection, crops, plan, onPlanChange }: Props) {
    const [dropAt, setDropAt] = useState<number | null>(null)
    const [dragging, setDragging] = useState<Dragged | null>(null)
    const full = plan.length >= MAX_PLAN_LENGTH

    // names the crops page already loaded; no request from this page
    const names = useMemo(() => {
        const known = new Map(
            (peekCrops(selection)?.crops ?? []).map((c) => [c.id, c.name]),
        )
        return (id: string) => known.get(id) ?? humanize(id)
    }, [selection])

    const insert = (id: string, at: number) => {
        if (full) return
        onPlanChange([...plan.slice(0, at), id, ...plan.slice(at)])
    }
    const remove = (index: number) =>
        onPlanChange(plan.filter((_, i) => i !== index))
    const move = (from: number, to: number) => {
        if (to < 0 || to > plan.length) return
        const next = [...plan]
        const [id] = next.splice(from, 1)
        next.splice(to > from ? to - 1 : to, 0, id)
        onPlanChange(next)
    }

    const startDrag = (e: DragEvent, dragged: Dragged) => {
        e.dataTransfer.setData(DRAG_TYPE, JSON.stringify(dragged))
        e.dataTransfer.effectAllowed = dragged.from === "plan" ? "move" : "copy"
        setDragging(dragged)
    }
    const endDrag = () => {
        setDragging(null)
        setDropAt(null)
    }

    const accepts = (e: DragEvent) =>
        e.dataTransfer.types.includes(DRAG_TYPE) &&
        !(dragging?.from === "option" && full)

    const overTrack = (e: DragEvent<HTMLElement>, at: number) => {
        if (!accepts(e)) return
        e.preventDefault()
        e.stopPropagation()
        e.dataTransfer.dropEffect = dragging?.from === "plan" ? "move" : "copy"
        if (at !== dropAt) setDropAt(at)
    }
    const dropOnTrack = (e: DragEvent) => {
        e.preventDefault()
        const dragged = readDragged(e)
        const at = dropAt ?? plan.length
        endDrag()
        if (!dragged) return
        if (dragged.from === "option") insert(dragged.id, at)
        else move(dragged.index, at)
    }

    // dragging a planned crop back to the options takes it out of the plan
    const overOptions = (e: DragEvent) => {
        if (dragging?.from !== "plan") return
        e.preventDefault()
        e.dataTransfer.dropEffect = "move"
    }
    const dropOnOptions = (e: DragEvent) => {
        e.preventDefault()
        const dragged = readDragged(e)
        endDrag()
        if (dragged?.from === "plan") remove(dragged.index)
    }

    return (
        <div className="plan-page">
            <header className="plan-header">
                <Link to="/crops" className="plan-back">
                    ← Back to crops
                </Link>
                <h1>Planned rotation</h1>
                <p className="plan-area">
                    Area: {describeSelection(selection)}
                </p>
                <p className="plan-intro">
                    Drag your crops into the plan in the order you will plant
                    them; a crop can go in more than once. Your plan is shown
                    first on the strategies page, and the other strategies are
                    compared with it.
                </p>
            </header>

            <section
                className={`plan-options${dragging?.from === "plan" ? " removing" : ""}`}
                onDragOver={overOptions}
                onDrop={dropOnOptions}
                aria-labelledby="plan-options-title"
            >
                <h2 id="plan-options-title">Your crops</h2>
                <p className="plan-hint">
                    {dragging?.from === "plan"
                        ? "Drop here to take it out of the plan"
                        : full
                          ? `The plan is full (${MAX_PLAN_LENGTH} plantings)`
                          : "Drag into the plan, or click to add at the end"}
                </p>
                <ul>
                    {crops.map((id) => (
                        <li key={id}>
                            <button
                                type="button"
                                className="plan-option"
                                draggable={!full}
                                disabled={full}
                                onDragStart={(e) =>
                                    startDrag(e, { from: "option", id })
                                }
                                onDragEnd={endDrag}
                                onClick={() => insert(id, plan.length)}
                                aria-label={`Add ${names(id)} to the end of the plan`}
                            >
                                <span aria-hidden="true">+</span>
                                {names(id)}
                            </button>
                        </li>
                    ))}
                </ul>
            </section>

            <section
                className={`plan-track${dropAt !== null ? " receiving" : ""}`}
                onDragOver={(e) => overTrack(e, dropAt ?? plan.length)}
                onDragLeave={(e) => {
                    if (!e.currentTarget.contains(e.relatedTarget as Node))
                        setDropAt(null)
                }}
                onDrop={dropOnTrack}
                aria-labelledby="plan-track-title"
            >
                <h2 id="plan-track-title">
                    Your plan
                    <span className="plan-length">
                        {plan.length} of {MAX_PLAN_LENGTH} plantings
                    </span>
                </h2>
                {plan.length === 0 ? (
                    <p className="plan-empty">
                        Drag crops here, first planting on the left
                    </p>
                ) : (
                    <ol>
                        {plan.map((id, i) => (
                            <li
                                key={`${i}-${id}`}
                                className={[
                                    "plan-step",
                                    dropAt === i && "drop-before",
                                    dropAt === plan.length &&
                                        i === plan.length - 1 &&
                                        "drop-after",
                                    dragging?.from === "plan" &&
                                        dragging.index === i &&
                                        "dragged",
                                ]
                                    .filter(Boolean)
                                    .join(" ")}
                                draggable
                                onDragStart={(e) =>
                                    startDrag(e, { from: "plan", index: i })
                                }
                                onDragEnd={endDrag}
                                onDragOver={(e) => overTrack(e, sideOf(e, i))}
                            >
                                <span className="plan-step-number">
                                    {i + 1}
                                </span>
                                <span className="plan-step-name">
                                    {names(id)}
                                </span>
                                <button
                                    type="button"
                                    disabled={i === 0}
                                    onClick={() => move(i, i - 1)}
                                    aria-label={`Plant ${names(id)} (planting ${i + 1}) earlier`}
                                >
                                    ‹
                                </button>
                                <button
                                    type="button"
                                    disabled={i === plan.length - 1}
                                    onClick={() => move(i, i + 2)}
                                    aria-label={`Plant ${names(id)} (planting ${i + 1}) later`}
                                >
                                    ›
                                </button>
                                <button
                                    type="button"
                                    onClick={() => remove(i)}
                                    aria-label={`Remove ${names(id)} (planting ${i + 1})`}
                                >
                                    ×
                                </button>
                            </li>
                        ))}
                    </ol>
                )}
            </section>

            <footer className="plan-footer">
                <button
                    type="button"
                    className="plan-clear"
                    disabled={plan.length === 0}
                    onClick={() => onPlanChange([])}
                >
                    Clear plan
                </button>
                <Link to="/strategy" className="plan-next">
                    {plan.length > 0
                        ? "Build strategies →"
                        : "Skip, build strategies →"}
                </Link>
            </footer>
        </div>
    )
}

export default PlanPage
