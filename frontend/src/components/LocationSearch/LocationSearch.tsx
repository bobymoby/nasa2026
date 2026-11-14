import { useEffect, useRef, useState } from "react"

const GEOCODING_URL = "https://api.mapbox.com/search/geocode/v6/forward"
const DEBOUNCE_MS = 300

export type SearchResult = {
    id: string
    name: string
    center: [number, number]
    // [west, south, east, north], only present for areas (cities, regions...)
    bbox?: [number, number, number, number]
}

type GeocodingFeature = {
    id: string
    geometry: { coordinates: [number, number] }
    properties: {
        full_address?: string
        name: string
        bbox?: [number, number, number, number]
    }
}

type Props = {
    accessToken: string
    // biases results towards what the user is looking at
    proximity?: [number, number]
    onSelect: (result: SearchResult) => void
}

function LocationSearch({ accessToken, proximity, onSelect }: Props) {
    const [query, setQuery] = useState("")
    const [results, setResults] = useState<SearchResult[]>([])
    const [highlighted, setHighlighted] = useState(0)
    const [error, setError] = useState<string | null>(null)
    // picking a result fills the input with its name; don't search for it again
    const selectedNameRef = useRef<string | null>(null)

    const proximityParam = proximity ? proximity.join(",") : undefined

    useEffect(() => {
        const q = query.trim()
        if (q.length < 2 || query === selectedNameRef.current) {
            setResults([])
            setError(null)
            return
        }

        const controller = new AbortController()
        const timeout = setTimeout(async () => {
            const params = new URLSearchParams({
                q,
                access_token: accessToken,
                autocomplete: "true",
                limit: "5",
            })
            if (proximityParam) params.set("proximity", proximityParam)

            try {
                const res = await fetch(`${GEOCODING_URL}?${params}`, {
                    signal: controller.signal,
                })
                if (!res.ok) throw new Error(`Search failed (${res.status})`)
                const data: { features: GeocodingFeature[] } = await res.json()

                setResults(
                    data.features.map((f) => ({
                        id: f.id,
                        name: f.properties.full_address ?? f.properties.name,
                        center: f.geometry.coordinates,
                        bbox: f.properties.bbox,
                    })),
                )
                setHighlighted(0)
                setError(null)
            } catch (err) {
                if (controller.signal.aborted) return
                setResults([])
                setError(err instanceof Error ? err.message : "Search failed")
            }
        }, DEBOUNCE_MS)

        return () => {
            clearTimeout(timeout)
            controller.abort()
        }
    }, [query, accessToken, proximityParam])

    const select = (result: SearchResult) => {
        onSelect(result)
        selectedNameRef.current = result.name
        setQuery(result.name)
        setResults([])
    }

    const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
        if (results.length === 0) return

        if (e.key === "ArrowDown") {
            e.preventDefault()
            setHighlighted((i) => (i + 1) % results.length)
        } else if (e.key === "ArrowUp") {
            e.preventDefault()
            setHighlighted((i) => (i - 1 + results.length) % results.length)
        } else if (e.key === "Enter") {
            e.preventDefault()
            select(results[highlighted])
        } else if (e.key === "Escape") {
            setResults([])
        }
    }

    return (
        <div className="location-search">
            <input
                type="search"
                placeholder="Search location..."
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={onKeyDown}
            />
            {results.length > 0 && (
                <ul className="location-search-results">
                    {results.map((result, i) => (
                        <li
                            key={result.id}
                            className={
                                i === highlighted ? "highlighted" : undefined
                            }
                            onMouseEnter={() => setHighlighted(i)}
                            // mousedown so it fires before the input loses focus
                            onMouseDown={(e) => {
                                e.preventDefault()
                                select(result)
                            }}
                        >
                            {result.name}
                        </li>
                    ))}
                </ul>
            )}
            {error && <div className="coord-error">{error}</div>}
        </div>
    )
}

export default LocationSearch
