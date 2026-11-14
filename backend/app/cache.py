"""Tiny JSON disk cache for upstream responses.

Historical climate and satellite data don't change, and ERA5 requests are slow
(~15 s for 44 years) and rate-limited, so responses are kept on disk.
"""

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

CACHE_DIR = Path(__file__).resolve().parents[1] / ".cache"


def cached_json(namespace: str, key: Any, fetch: Callable[[], Any]) -> Any:
    digest = hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:24]
    path = CACHE_DIR / namespace / f"{digest}.json"
    if path.exists():
        return json.loads(path.read_text())
    value = fetch()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value))
    tmp.replace(path)
    return value
