from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # backend/.env first, then the repo-root .env (later files take priority)
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    app_name: str = "NASA Backend"
    cors_origins: list[str] = ["http://localhost:3000"]

    # Sentinel Hub OAuth client credentials
    sh_client_id: str | None = None
    sh_client_secret: str | None = None
    sh_deployment: Literal["cdse", "sh"] = "cdse"


settings = Settings()
