"""
main.py — FastAPI entry point

Wires the app together: auth middleware, rate limiting, CORS, routers,
lifespan model warm, and optional static file serving on HF Spaces.

CIRCULAR IMPORT NOTE:
The rate limiter is defined in limiter.py, not here. This is deliberate —
routers need to import the limiter, and if it were defined in main.py,
importing it from routers would create a circular dependency
(main → routers → main). limiter.py has no such dependencies.
"""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.api.limiter import limiter
from app.api.routers import documents, query
from app.ingestion.embedder import get_model

# Load .env relative to this file — working-directory-independent
load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent.parent / ".env")

# ── API key auth ──────────────────────────────────────────────────────────────
DOCMIND_API_KEY = os.environ.get("DOCMIND_API_KEY", "").strip()
_AUTH_EXEMPT = {"/health", "/docs", "/openapi.json", "/redoc"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not DOCMIND_API_KEY:
        print("WARNING: DOCMIND_API_KEY is not set — endpoints are unprotected")
    else:
        print(f"API key auth enabled (key length: {len(DOCMIND_API_KEY)} chars)")

    print("Loading embedding model...")
    get_model()
    print("Model ready — accepting requests")
    yield


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="DocMind API",
    description="Multi-document RAG system with citations and confidence signaling.",
    version="0.1.0",
    lifespan=lifespan,
)

# Rate limiter
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def require_api_key(request: Request, call_next):
    if request.url.path in _AUTH_EXEMPT:
        return await call_next(request)
    if not DOCMIND_API_KEY:
        return await call_next(request)
    provided_key = request.headers.get("X-API-Key", "").strip()
    if provided_key != DOCMIND_API_KEY:
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid or missing API key. Set X-API-Key header."},
        )
    return await call_next(request)


# Routers
app.include_router(documents.router)
app.include_router(query.router)

# On HF Spaces, serve the built React frontend as static files
if os.environ.get("HF_SPACE", "").lower() == "true":
    from fastapi.staticfiles import StaticFiles
    _static = Path(__file__).parent.parent.parent / "static"
    if _static.exists():
        app.mount("/", StaticFiles(directory=str(_static), html=True), name="static")


@app.get("/health")
def health_check():
    """Liveness check — no auth required."""
    return {"status": "ok"}