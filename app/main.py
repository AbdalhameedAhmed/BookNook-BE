from __future__ import annotations

import sys
from pathlib import Path

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

sys.path.insert(0, str(Path(__file__).resolve().parent))

from api.v1.api import api_router
from core.auth_settings import get_auth_settings


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    get_auth_settings()  # Fail fast on missing/invalid auth or email configuration.
    yield


app = FastAPI(
    lifespan=lifespan,
    title="BookNook API",
    version="0.1.0",
    description="Backend for the BookNook marketplace MVP.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok", "service": "booknook-backend"}


@app.get("/api/v1/health")
def api_health_check() -> dict[str, str]:
    return {"status": "ok", "service": "booknook-backend"}