from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.config import settings
from app.db import pool
from app.routers import climate, crops, drought, fields, health, sentinel
from app.timing import setup as setup_logging

setup_logging(settings.log_level)


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool.open()
    yield
    pool.close()


app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api")
app.include_router(crops.router, prefix="/api")
app.include_router(sentinel.router, prefix="/api")
app.include_router(climate.router, prefix="/api")
app.include_router(drought.router, prefix="/api")
app.include_router(fields.router, prefix="/api")


@app.get("/")
def root() -> dict[str, str]:
    return {"message": f"{settings.app_name} is running"}
