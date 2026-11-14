"""JSON cache for slow upstream responses, stored in Postgres.

Historical climate data and soil classes don't change, and the upstream APIs
are slow and rate-limited (ERA5 ~15 s for 44 years, SoilGrids ~4 s and about
5 requests a minute), so answers are kept in the `json_cache` table:

    namespace  era5 | power | soilgrids ...
    key_hash   sha256 of the request key (same digest the old .cache/ files used,
               so `python -m app.import_file_cache` can load them)
    key        the request key itself, for inspecting the table
    value      the cached response

Entries never expire. If the database is unreachable the cache is skipped and
the upstream is called directly.
"""

import hashlib
import json
import logging
import time
from typing import Any, Callable

from app.db import ensure_table, pool
from app.timing import emit

log = logging.getLogger("app.cache")

DDL = """
CREATE TABLE IF NOT EXISTS json_cache (
    namespace  text        NOT NULL,
    key_hash   text        NOT NULL,
    key        jsonb,
    value      jsonb       NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (namespace, key_hash)
)
"""

PUT_SQL = """
INSERT INTO json_cache (namespace, key_hash, key, value) VALUES (%s, %s, %s, %s)
ON CONFLICT (namespace, key_hash) DO UPDATE
SET key = EXCLUDED.key, value = EXCLUDED.value, created_at = now()
"""


def key_hash(key: Any) -> str:
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:24]


def _get(namespace: str, digest: str) -> tuple[bool, Any]:
    try:
        ensure_table("json_cache", DDL)
        with pool.connection(timeout=5) as conn:
            row = conn.execute(
                "SELECT value FROM json_cache WHERE namespace = %s AND key_hash = %s",
                (namespace, digest),
            ).fetchone()
        return (True, row[0]) if row else (False, None)
    except Exception as e:  # cache must never break the request
        log.warning("cache read failed (%s), calling upstream: %s", namespace, e)
        return False, None


def put(namespace: str, digest: str, value: Any, key: Any = None) -> None:
    try:
        ensure_table("json_cache", DDL)
        with pool.connection(timeout=5) as conn:
            conn.execute(PUT_SQL, (
                namespace, digest,
                json.dumps(key, allow_nan=False) if key is not None else None,
                json.dumps(value, allow_nan=False),  # Postgres JSON has no NaN
            ))
    except Exception as e:
        log.warning("cache write failed (%s): %s", namespace, e)


def cached_json(namespace: str, key: Any, fetch: Callable[[], Any]) -> Any:
    digest = key_hash(key)
    start = time.perf_counter()
    found, value = _get(namespace, digest)
    if found:
        emit(f"cache hit {namespace}", (time.perf_counter() - start) * 1000)
        return value
    emit(f"cache miss {namespace}", key=digest)
    value = fetch()
    start = time.perf_counter()
    put(namespace, digest, value, key)
    emit(f"cache write {namespace}", (time.perf_counter() - start) * 1000)
    return value
