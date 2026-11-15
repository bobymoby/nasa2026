import { API_URL } from "./apiUrl"
import { Field } from "./fetchField"
import { toSelectedField } from "./fetchFieldNdvi"

export function prefetchSiteContext(fields: Field[]): void {
    fetch(`${API_URL}/api/fields/strategy-context/prefetch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fields: fields.map(toSelectedField) }),
    }).catch(() => {})
}
