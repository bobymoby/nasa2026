from psycopg_pool import ConnectionPool

from app.config import settings

# opened and closed by the app lifespan in main.py
pool = ConnectionPool(settings.database_url, min_size=1, max_size=10, open=False)
