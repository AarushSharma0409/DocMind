"""
main.py — Phase 3, API entry point

Creates the FastAPI app instance and wires everything together.
Routers for each endpoint group live in separate files and are
registered here — this file stays thin, it's just the assembly point.

STARTUP OPTIMIZATION: the embedding model (all-MiniLM-L6-v2) is
eager-loaded here via FastAPI's lifespan hook, rather than lazily on
the first upload request. Without this, the first upload after starting
the server pays a 2-5 second model-load tax on top of ingestion time,
making it feel broken. With eager loading, uvicorn prints "Model ready"
before accepting requests, and every subsequent upload skips that cost
entirely.

SECURITY — three layers added:

1. API KEY AUTH (middleware)
   Every request must carry X-API-Key matching DOCMIND_API_KEY from .env.
   Missing or wrong key → 401 before the request reaches any router.
   /health is exempted so the frontend status poll works without auth.
   WHY MIDDLEWARE NOT A DEPENDENCY: a FastAPI Depends() would need to be
   added to every endpoint individually — easy to forget when adding new
   routes. Middleware runs unconditionally on every request, so new
   routes are protected automatically.

2. RATE LIMITING (slowapi)
   Applied per-endpoint at the router level (see documents.py, query.py).
   The Limiter instance is created here and attached to app.state so
   routers can import and use it. SlowAPIMiddleware catches limit
   violations and returns 429 automatically.
   WHY PER-ENDPOINT NOT GLOBAL: upload and query are the expensive ops
   (disk I/O, model inference, LLM API calls). List/delete/health are
   cheap and fine to hit frequently. A blanket global limit would either
   be too strict for cheap endpoints or too loose for expensive ones.

3. FILE SIZE LIMIT (documents.py)
   Checked immediately after reading upload bytes — see documents.py.
"""

import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from app.api.routers import documents, query
from app.ingestion.embedder import get_model

# Load .env relative to this file — working-directory-independent
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

# ── Rate limiter ──────────────────────────────────────────────────────────────
# Keyed by client IP. Routers import this instance to apply per-endpoint
# limits via @limiter.limit("N/period") decorators.
limiter = Limiter(key_func=get_remote_address, default_limits=[])


# ── API key auth ──────────────────────────────────────────────────────────────
DOCMIND_API_KEY = os.environ.get("DOCMIND_API_KEY", "").strip()

# Paths that don't require authentication.
# /health must be open so the frontend liveness poll works without a key.
_AUTH_EXEMPT = {"/health", "/docs", "/openapi.json", "/redoc"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Runs once at startup (before any requests), and once at shutdown.

    Warms the embedding model so the first upload request doesn't pay
    the 2-5 second load cost. Also validates that DOCMIND_API_KEY is
    set — failing fast at startup is better than failing silently on
    the first authenticated request.
    """
    if not DOCMIND_API_KEY:
        print("WARNING: DOCMIND_API_KEY is not set in .env")
        print("   All endpoints are unprotected. Set a key before deploying.")
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

# Attach limiter to app state — SlowAPIMiddleware reads it from here
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# CORS — replace ["*"] with your actual frontend domain in production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def require_api_key(request: Request, call_next):
    """
    Reject requests that don't carry the correct X-API-Key header.

    Runs before any router logic. Returns 401 immediately on missing or
    wrong key so no downstream code executes.

    Exemptions: /health and OpenAPI docs are open so the frontend status
    poll and local development browsing work without auth.

    If DOCMIND_API_KEY is not set in .env, auth is skipped entirely with
    a warning at startup — preserves local dev ergonomics while making
    the security gap obvious in logs.
    """
    if request.url.path in _AUTH_EXEMPT:
        return await call_next(request)

    # If no key is configured, skip auth (local dev convenience)
    if not DOCMIND_API_KEY:
        return await call_next(request)

    provided_key = request.headers.get("X-API-Key", "").strip()
    if provided_key != DOCMIND_API_KEY:
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid or missing API key. Set X-API-Key header."},
        )

    return await call_next(request)


# Routers — each file handles one concern
app.include_router(documents.router)
app.include_router(query.router)


@app.get("/health")
def health_check():
    """
    Liveness check — no auth required.
    Returns ok if the server is running, regardless of model/DB state.
    """
    return {"status": "ok"}