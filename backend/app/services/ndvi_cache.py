"""Postgres cache for per-year Sentinel-2 field observations.

One row per (plot key, year, evalscript version) holding that year's parsed
acquisitions. The plot key is the field id when the frontend sends one, so the
same parcel hits the cache however its outline is serialised.

Freshness: a year that had already ended when it was fetched never changes,
so it is kept forever. The current year (or one fetched before it ended) is
refetched after CURRENT_YEAR_TTL.

Concurrent requests for the same plot and year (a prefetch still running when
Run is pressed) share one Copernicus request instead of making two.

If the database is unreachable the cache is skipped, not the request.
"""

import json
import logging
import threading
from concurrent.futures import Future
from typing import Callable

from app.db import ensure_table, pool
from app.timing import emit

log = logging.getLogger("app.ndvi_cache")

CURRENT_YEAR_TTL = "1 day"

DDL = """
CREATE TABLE IF NOT EXISTS ndvi_year_cache (
    cache_key  text        NOT NULL,
    year       integer     NOT NULL,
    version    text        NOT NULL,
    rows       jsonb       NOT NULL,
    fetched_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (cache_key, year, version)
)
"""

GET_SQL = f"""
SELECT rows FROM ndvi_year_cache
WHERE cache_key = %s AND year = %s AND version = %s
  AND (year < extract(year FROM fetched_at)            -- year was over when fetched
       OR fetched_at > now() - interval '{CURRENT_YEAR_TTL}')
"""

PUT_SQL = """
INSERT INTO ndvi_year_cache (cache_key, year, version, rows, fetched_at)
VALUES (%s, %s, %s, %s, now())
ON CONFLICT (cache_key, year, version)
DO UPDATE SET rows = EXCLUDED.rows, fetched_at = EXCLUDED.fetched_at
"""

_inflight: dict[tuple, Future] = {}
_inflight_lock = threading.Lock()


def _ensure_table() -> None:
    ensure_table("ndvi_year_cache", DDL)


def _get(key: str, year: int, version: str) -> list[dict] | None:
    try:
        _ensure_table()
        with pool.connection(timeout=5) as conn:
            row = conn.execute(GET_SQL, (key, year, version)).fetchone()
        return row[0] if row else None
    except Exception as e:  # cache must never break the request
        log.warning("ndvi cache read failed, fetching instead: %s", e)
        return None


def _put(key: str, year: int, version: str, rows: list[dict]) -> None:
    try:
        _ensure_table()
        with pool.connection(timeout=5) as conn:
            conn.execute(PUT_SQL, (key, year, version, json.dumps(rows)))
    except Exception as e:
        log.warning("ndvi cache write failed: %s", e)


def get_or_fetch(
    key: str, year: int, version: str, fetch: Callable[[], list[dict]]
) -> list[dict]:
    ident = (key, year, version)
    with _inflight_lock:
        running = _inflight.get(ident)
        if running is None:
            mine: Future = Future()
            _inflight[ident] = mine
    if running is not None:
        emit(f"wait for in-flight {year}")
        return running.result()

    try:
        rows = _get(key, year, version)
        if rows is not None:
            emit(f"db cache hit {year}", rows=len(rows))
        else:
            emit(f"db cache miss {year}", key=key)
            rows = fetch()
            _put(key, year, version, rows)
        mine.set_result(rows)
        return rows
    except BaseException as e:
        mine.set_exception(e)
        raise
    finally:
        with _inflight_lock:
            _inflight.pop(ident, None)
