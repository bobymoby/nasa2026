"""One-off: copy the old backend/.cache/ JSON files into the Postgres cache.

    cd backend && .venv/bin/python -m app.import_file_cache

The file names are the same key hashes `cached_json` uses, so imported entries
are hit by the same requests. Existing rows are left alone. Afterwards the
.cache/ folder can be deleted.

s2_field and s2_ndvi are skipped: field NDVI now has its own table with a
different key, so those files would never be read.
"""

import json
from pathlib import Path

from app.cache import DDL, PUT_SQL
from app.db import pool

CACHE_DIR = Path(__file__).resolve().parents[1] / ".cache"
SKIP = {"s2_field", "s2_ndvi"}


def main() -> None:
    if not CACHE_DIR.exists():
        print(f"nothing to import: {CACHE_DIR} does not exist")
        return
    pool.open(wait=True, timeout=10)
    try:
        with pool.connection() as conn:
            conn.execute(DDL)
            for ns_dir in sorted(p for p in CACHE_DIR.iterdir() if p.is_dir()):
                if ns_dir.name in SKIP:
                    print(f"{ns_dir.name}: skipped (obsolete)")
                    continue
                added = 0
                for f in sorted(ns_dir.glob("*.json")):
                    exists = conn.execute(
                        "SELECT 1 FROM json_cache WHERE namespace = %s AND key_hash = %s",
                        (ns_dir.name, f.stem),
                    ).fetchone()
                    if exists:
                        continue
                    value = json.loads(f.read_text())
                    conn.execute(PUT_SQL, (ns_dir.name, f.stem, None, json.dumps(value)))
                    added += 1
                print(f"{ns_dir.name}: {added} imported")
    finally:
        pool.close()


if __name__ == "__main__":
    main()
