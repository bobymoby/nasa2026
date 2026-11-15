"""Postgres tables for the price pipeline: the crop mapping and each source's raw series, yields included.

Every source is stored as it arrives (nominal, in its own unit) with a fetch
time, so standardising can change without refetching. If the database is
unreachable, reads return None and writes are skipped; the pipeline then keeps
the fetched data in memory.
"""

import logging
import time
from collections.abc import Iterable

from app.db import ensure_table, pool
from app.services.prices.sources import EurostatPrice, EurostatYield, FaoPrice, FaoYield

log = logging.getLogger("app.prices")

DB_TIMEOUT = 5
DB_RETRY_S = 60

DDL = {
    "price_crop": """
        CREATE TABLE IF NOT EXISTS price_crop (
            crop_id           text PRIMARY KEY,
            crop_name         text NOT NULL,
            eurostat_prod_veg text,
            fao_item_code     integer
        )""",
    "price_fetch": """
        CREATE TABLE IF NOT EXISTS price_fetch (
            source     text NOT NULL,
            key        text NOT NULL,
            fetched_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (source, key)
        )""",
    "eurostat_crop_price": """
        CREATE TABLE IF NOT EXISTS eurostat_crop_price (
            geo       text NOT NULL,
            prod_veg  text NOT NULL,
            year      integer NOT NULL,
            eur_per_t double precision NOT NULL,
            flag      text,
            PRIMARY KEY (geo, prod_veg, year)
        )""",
    "fao_producer_price": """
        CREATE TABLE IF NOT EXISTS fao_producer_price (
            m49       integer NOT NULL,
            item_code integer NOT NULL,
            year      integer NOT NULL,
            usd_per_t double precision NOT NULL,
            flag      text NOT NULL,
            PRIMARY KEY (m49, item_code, year)
        )""",
    "eurostat_crop_yield": """
        CREATE TABLE IF NOT EXISTS eurostat_crop_yield (
            geo   text NOT NULL,
            crops text NOT NULL,
            year  integer NOT NULL,
            t_ha  double precision NOT NULL,
            flag  text,
            PRIMARY KEY (geo, crops, year)
        )""",
    "fao_crop_yield": """
        CREATE TABLE IF NOT EXISTS fao_crop_yield (
            m49       integer NOT NULL,
            item_code integer NOT NULL,
            year      integer NOT NULL,
            t_ha      double precision NOT NULL,
            flag      text NOT NULL,
            PRIMARY KEY (m49, item_code, year)
        )""",
    "eurostat_fx": """
        CREATE TABLE IF NOT EXISTS eurostat_fx (
            currency text NOT NULL,
            year     integer NOT NULL,
            per_eur  double precision NOT NULL,
            PRIMARY KEY (currency, year)
        )""",
    "eurostat_hicp": """
        CREATE TABLE IF NOT EXISTS eurostat_hicp (
            geo   text NOT NULL,
            year  integer NOT NULL,
            index double precision NOT NULL,
            PRIMARY KEY (geo, year)
        )""",
}

_down_until = 0.0


class DbSkipped(RuntimeError):
    pass


def _up() -> None:
    if time.monotonic() < _down_until:
        raise DbSkipped("database marked unreachable")
    for name, ddl in DDL.items():
        ensure_table(name, ddl)


def _failed(what: str, e: Exception) -> None:
    global _down_until
    if isinstance(e, DbSkipped):
        return
    _down_until = time.monotonic() + DB_RETRY_S
    log.warning("prices %s failed, skipping the DB for %ss: %s", what, DB_RETRY_S, e)


def is_fresh(source: str, key: str, max_age_days: int) -> bool | None:
    """True/False from price_fetch, None when the database can't say."""
    try:
        _up()
        with pool.connection(timeout=DB_TIMEOUT) as conn:
            row = conn.execute(
                "SELECT fetched_at > now() - make_interval(days => %s) FROM price_fetch "
                "WHERE source = %s AND key = %s",
                (max_age_days, source, key),
            ).fetchone()
        return bool(row and row[0])
    except Exception as e:
        _failed("freshness read", e)
        return None


def _mark(cur, source: str, key: str) -> None:
    cur.execute(
        """INSERT INTO price_fetch (source, key) VALUES (%s, %s)
           ON CONFLICT (source, key) DO UPDATE SET fetched_at = now()""",
        (source, key),
    )


def sync_crops(rows: Iterable[tuple[str, str, str | None, int | None]]) -> None:
    try:
        _up()
        with pool.connection(timeout=DB_TIMEOUT) as conn, conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO price_crop (crop_id, crop_name, eurostat_prod_veg, fao_item_code)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (crop_id) DO UPDATE SET crop_name = EXCLUDED.crop_name,
                       eurostat_prod_veg = EXCLUDED.eurostat_prod_veg,
                       fao_item_code = EXCLUDED.fao_item_code""",
                list(rows),
            )
    except Exception as e:
        _failed("crop mapping write", e)


def put_eurostat(geo: str, prices: list[EurostatPrice]) -> None:
    try:
        _up()
        with pool.connection(timeout=DB_TIMEOUT) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM eurostat_crop_price WHERE geo = %s", (geo,))
            cur.executemany(
                "INSERT INTO eurostat_crop_price (geo, prod_veg, year, eur_per_t, flag) VALUES (%s, %s, %s, %s, %s)",
                [(geo, p.prod_veg, p.year, p.eur_per_t, p.flag) for p in prices],
            )
            _mark(cur, "eurostat", geo)
    except Exception as e:
        _failed("eurostat write", e)


def get_eurostat(geo: str) -> list[EurostatPrice] | None:
    try:
        _up()
        with pool.connection(timeout=DB_TIMEOUT) as conn:
            rows = conn.execute(
                "SELECT prod_veg, year, eur_per_t, flag FROM eurostat_crop_price WHERE geo = %s", (geo,)
            ).fetchall()
        return [EurostatPrice(*r) for r in rows]
    except Exception as e:
        _failed("eurostat read", e)
        return None


def put_fao(prices: list[FaoPrice]) -> None:
    try:
        _up()
        with pool.connection(timeout=DB_TIMEOUT) as conn, conn.cursor() as cur:
            cur.execute("TRUNCATE fao_producer_price")
            with cur.copy("COPY fao_producer_price (m49, item_code, year, usd_per_t, flag) FROM STDIN") as copy:
                for p in prices:
                    copy.write_row((p.m49, p.item_code, p.year, p.usd_per_t, p.flag))
            _mark(cur, "faostat", "bulk")
    except Exception as e:
        _failed("faostat write", e)


def has_fao() -> bool:
    try:
        _up()
        with pool.connection(timeout=DB_TIMEOUT) as conn:
            return conn.execute("SELECT EXISTS (SELECT 1 FROM fao_producer_price)").fetchone()[0]
    except Exception as e:
        _failed("faostat read", e)
        return False


def get_fao(m49: int) -> list[FaoPrice] | None:
    try:
        _up()
        with pool.connection(timeout=DB_TIMEOUT) as conn:
            rows = conn.execute(
                "SELECT m49, item_code, year, usd_per_t, flag FROM fao_producer_price WHERE m49 = %s", (m49,)
            ).fetchall()
        return [FaoPrice(*r) for r in rows]
    except Exception as e:
        _failed("faostat read", e)
        return None


def put_eurostat_yields(geo: str, yields: list[EurostatYield]) -> None:
    try:
        _up()
        with pool.connection(timeout=DB_TIMEOUT) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM eurostat_crop_yield WHERE geo = %s", (geo,))
            cur.executemany(
                "INSERT INTO eurostat_crop_yield (geo, crops, year, t_ha, flag) VALUES (%s, %s, %s, %s, %s)",
                [(geo, y.crops, y.year, y.t_ha, y.flag) for y in yields],
            )
            _mark(cur, "eurostat_yield", geo)
    except Exception as e:
        _failed("eurostat yield write", e)


def get_eurostat_yields(geo: str) -> list[EurostatYield] | None:
    try:
        _up()
        with pool.connection(timeout=DB_TIMEOUT) as conn:
            rows = conn.execute(
                "SELECT crops, year, t_ha, flag FROM eurostat_crop_yield WHERE geo = %s", (geo,)
            ).fetchall()
        return [EurostatYield(*r) for r in rows]
    except Exception as e:
        _failed("eurostat yield read", e)
        return None


def put_fao_yields(yields: list[FaoYield]) -> None:
    try:
        _up()
        with pool.connection(timeout=DB_TIMEOUT) as conn, conn.cursor() as cur:
            cur.execute("TRUNCATE fao_crop_yield")
            with cur.copy("COPY fao_crop_yield (m49, item_code, year, t_ha, flag) FROM STDIN") as copy:
                for y in yields:
                    copy.write_row((y.m49, y.item_code, y.year, y.t_ha, y.flag))
            _mark(cur, "faostat_yield", "bulk")
    except Exception as e:
        _failed("faostat yield write", e)


def has_fao_yields() -> bool:
    try:
        _up()
        with pool.connection(timeout=DB_TIMEOUT) as conn:
            return conn.execute("SELECT EXISTS (SELECT 1 FROM fao_crop_yield)").fetchone()[0]
    except Exception as e:
        _failed("faostat yield read", e)
        return False


def get_fao_yields(m49: int) -> list[FaoYield] | None:
    try:
        _up()
        with pool.connection(timeout=DB_TIMEOUT) as conn:
            rows = conn.execute(
                "SELECT m49, item_code, year, t_ha, flag FROM fao_crop_yield WHERE m49 = %s", (m49,)
            ).fetchall()
        return [FaoYield(*r) for r in rows]
    except Exception as e:
        _failed("faostat yield read", e)
        return None


def put_series(table: str, key_col: str, key: str, values: dict[int, float], source: str) -> None:
    value_col = {"eurostat_fx": "per_eur", "eurostat_hicp": "index"}[table]
    try:
        _up()
        with pool.connection(timeout=DB_TIMEOUT) as conn, conn.cursor() as cur:
            cur.execute(f"DELETE FROM {table} WHERE {key_col} = %s", (key,))
            cur.executemany(
                f"INSERT INTO {table} ({key_col}, year, {value_col}) VALUES (%s, %s, %s)",
                [(key, y, v) for y, v in values.items()],
            )
            _mark(cur, source, key)
    except Exception as e:
        _failed(f"{table} write", e)


def get_series(table: str, key_col: str, key: str) -> dict[int, float] | None:
    value_col = {"eurostat_fx": "per_eur", "eurostat_hicp": "index"}[table]
    try:
        _up()
        with pool.connection(timeout=DB_TIMEOUT) as conn:
            rows = conn.execute(f"SELECT year, {value_col} FROM {table} WHERE {key_col} = %s", (key,)).fetchall()
        return dict(rows)
    except Exception as e:
        _failed(f"{table} read", e)
        return None
