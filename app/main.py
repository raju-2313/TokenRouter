"""FastAPI entrypoint for TokenRouter.

Milestone 2: OpenAI-compatible endpoints on top of multi-account,
token-aware routing with local usage accounting.
"""

from __future__ import annotations

from fastapi import FastAPI

from app import __version__
from app.api.routes import router

app = FastAPI(
    title="TokenRouter",
    version=__version__,
    description="Local OpenAI-compatible gateway routing to Clean APIs.",
)

app.include_router(router)


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe — never depends on upstream."""
    return {"status": "ok", "version": __version__}


@app.get("/ready")
async def ready() -> dict[str, str]:
    """Readiness — local only; the accounting database opens on first use."""
    return {"status": "ready", "version": __version__}


@app.get("/")
async def root() -> dict[str, str]:
    return {"name": "TokenRouter", "version": __version__, "health": "/health"}
