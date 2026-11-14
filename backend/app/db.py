import threading

from psycopg_pool import ConnectionPool

from app.config import settings

# opened and closed by the app lifespan in main.py
pool = ConnectionPool(settings.database_url, min_size=1, max_size=10, open=False)


_created: set[str] = set()
_created_lock = threading.Lock()


def ensure_table(name: str, ddl: str) -> None:
    """Run a CREATE TABLE IF NOT EXISTS once per process (caches create their own tables)."""
    if name in _created:
        return
    # concurrent CREATE TABLE IF NOT EXISTS can still collide in Postgres
    with _created_lock:
        if name not in _created:
            with pool.connection(timeout=5) as conn:
                conn.execute(ddl)
            _created.add(name)
