// Responses kept for the session, so moving back and forth between pages
// renders at once. Failed requests are dropped, so a retry asks again.
const pending = new Map<string, Promise<unknown>>()
const done = new Map<string, unknown>()

export function peek<T>(key: string): T | undefined {
    return done.get(key) as T | undefined
}

// the request runs without the caller's signal so other callers can share it;
// aborting only stops this caller from waiting
export function memo<T>(
    key: string,
    load: () => Promise<T>,
    signal?: AbortSignal,
): Promise<T> {
    let shared = pending.get(key) as Promise<T> | undefined
    if (!shared) {
        shared = load().then(
            (value) => {
                done.set(key, value)
                return value
            },
            (err) => {
                pending.delete(key)
                throw err
            },
        )
        pending.set(key, shared)
    }
    const request = shared
    if (!signal) return request
    return new Promise<T>((resolve, reject) => {
        const abort = () =>
            reject(new DOMException("The request was aborted.", "AbortError"))
        if (signal.aborted) return abort()
        signal.addEventListener("abort", abort, { once: true })
        request
            .then(resolve, reject)
            .finally(() => signal.removeEventListener("abort", abort))
    })
}
