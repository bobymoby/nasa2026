"""Tiny JSON disk cache for upstream responses.

Historical climate and satellite data don't change, and ERA5 requests are slow
(~15 s for 44 years) and rate-limited, so responses are kept on disk.
"""

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable

from app.timing import emit

CACHE_DIR = Path(__file__).resolve().parents[1] / ".cache"


def cached_json(namespace: str, key: Any, fetch: Callable[[], Any]) -> Any:
    digest = hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:24]
    path = CACHE_DIR / namespace / f"{digest}.json"
    start = time.perf_counter()
    if path.exists():
        text = path.read_text()
        value = json.loads(text)
        emit(f"cache hit {namespace}", (time.perf_counter() - start) * 1000, kb=len(text) // 1024)
        return value
    emit(f"cache miss {namespace}", key=digest)
    value = fetch()
    start = time.perf_counter()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value))
    tmp.replace(path)
    emit(f"cache write {namespace}", (time.perf_counter() - start) * 1000)
    return value
