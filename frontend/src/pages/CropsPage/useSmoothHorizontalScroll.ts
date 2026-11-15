import { RefObject, useCallback, useEffect, useRef, useState } from "react"

// share of the remaining distance covered each frame; higher = snappier
const EASE = 0.18
// keeps the end of a glide from crawling a pixel per frame
const MIN_STEP = 3

// Eased sideways scrolling for an overflow-x container: a vertical mouse
// wheel scrolls it horizontally, and scrollBy() glides instead of jumping.
// Any scrolling the user does directly (scrollbar, trackpad, keys) cancels
// the glide so the two never fight.
export function useSmoothHorizontalScroll(
    ref: RefObject<HTMLElement | null>,
    // re-attach when the element is (re)mounted
    deps: unknown[] = [],
) {
    const target = useRef(0)
    const frame = useRef<number | null>(null)
    // where the glide last put scrollLeft, to tell our scrolls from the user's
    const lastSet = useRef<number | null>(null)
    const [edges, setEdges] = useState({ atStart: true, atEnd: true })

    const stop = useCallback(() => {
        if (frame.current !== null) cancelAnimationFrame(frame.current)
        frame.current = null
        lastSet.current = null
    }, [])

    const animate = useCallback(() => {
        const el = ref.current
        if (!el) return stop()
        const distance = target.current - el.scrollLeft
        // browsers round scrollLeft to whole (device) pixels, so tiny steps
        // are lost; move at least MIN_STEP px and finish within it
        if (Math.abs(distance) <= MIN_STEP) {
            el.scrollLeft = target.current
            return stop()
        }
        const step = distance * EASE
        const before = el.scrollLeft
        el.scrollLeft =
            before +
            (Math.abs(step) < MIN_STEP ? Math.sign(step) * MIN_STEP : step)
        // stuck against an edge (e.g. the content shrank)
        if (el.scrollLeft === before) return stop()
        lastSet.current = el.scrollLeft
        frame.current = requestAnimationFrame(animate)
    }, [ref, stop])

    const scrollTo = useCallback(
        (left: number) => {
            const el = ref.current
            if (!el) return
            const max = el.scrollWidth - el.clientWidth
            target.current = Math.max(0, Math.min(max, left))
            if (frame.current === null)
                frame.current = requestAnimationFrame(animate)
        },
        [ref, animate],
    )

    const scrollBy = useCallback(
        (delta: number) => {
            const el = ref.current
            if (!el) return
            // keep going from where an ongoing glide is heading
            const from = frame.current === null ? el.scrollLeft : target.current
            scrollTo(from + delta)
        },
        [ref, scrollTo],
    )

    // jump without animating, e.g. back to the start after a new search
    const reset = useCallback(() => {
        stop()
        target.current = 0
        if (ref.current) ref.current.scrollLeft = 0
    }, [ref, stop])

    useEffect(() => {
        const el = ref.current
        if (!el) return

        const updateEdges = () => {
            const max = el.scrollWidth - el.clientWidth
            const atStart = el.scrollLeft <= 1
            const atEnd = el.scrollLeft >= max - 1
            // runs on every scroll event: only re-render when an edge flips
            setEdges((prev) =>
                prev.atStart === atStart && prev.atEnd === atEnd
                    ? prev
                    : { atStart, atEnd },
            )
        }

        const onWheel = (e: WheelEvent) => {
            // sideways swipes already scroll natively
            if (Math.abs(e.deltaY) <= Math.abs(e.deltaX)) return
            if (el.scrollWidth <= el.clientWidth) return
            e.preventDefault()
            // line-based wheels (Firefox) report lines, not pixels
            const delta = e.deltaMode === 1 ? e.deltaY * 40 : e.deltaY
            scrollBy(delta * 1.5)
        }

        const onScroll = () => {
            // a scroll we didn't make while gliding: the user took over
            if (
                frame.current !== null &&
                lastSet.current !== null &&
                Math.abs(el.scrollLeft - lastSet.current) > 2
            ) {
                stop()
            }
            if (frame.current === null) target.current = el.scrollLeft
            updateEdges()
        }

        // grabbing the scrollbar or touching the grid hands control back
        const onPointerDown = () => stop()

        // the box resizing or the content changing width (e.g. a new search)
        const resize = new ResizeObserver(updateEdges)
        resize.observe(el)
        if (el.firstElementChild) resize.observe(el.firstElementChild)
        updateEdges()

        el.addEventListener("wheel", onWheel, { passive: false })
        el.addEventListener("scroll", onScroll, { passive: true })
        el.addEventListener("pointerdown", onPointerDown)
        return () => {
            resize.disconnect()
            el.removeEventListener("wheel", onWheel)
            el.removeEventListener("scroll", onScroll)
            el.removeEventListener("pointerdown", onPointerDown)
            stop()
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [ref, scrollBy, stop, ...deps])

    return { scrollBy, reset, ...edges }
}
