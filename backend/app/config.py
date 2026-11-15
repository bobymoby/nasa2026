from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # backend/.env first, then the repo-root .env (later files take priority)
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    app_name: str = "NASA Backend"
    cors_origins: list[str] = ["http://localhost:3000"]

    # Sentinel Hub OAuth client credentials
    # TEMPORARY: read Danny's credentials (DANNY_SH_*) instead of SH_*.
    # Remove the validation_alias arguments to go back to SH_CLIENT_ID/SECRET.
    sh_client_id: str | None = Field(default=None, validation_alias="DANNY_SH_CLIENT_ID")
    sh_client_secret: str | None = Field(
        default=None, validation_alias="DANNY_SH_CLIENT_SECRET"
    )
    sh_deployment: Literal["cdse", "sh"] = "cdse"

    # app.* log level; DEBUG shows per-request timings (see app/timing.py)
    log_level: str = "DEBUG"

    # PostGIS database from the repo-root docker-compose.yml
    database_url: str = "postgresql://nasa:nasa@10.232.178.179:5432/nasa"


settings = Settings()
