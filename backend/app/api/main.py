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
# Static frontend routes and API docs are exempt from auth.
# All /api/ routes still require X-API-Key.
_AUTH_EXEMPT = {"/health", "/docs", "/openapi.json", "/redoc"}

def _is_exempt(path: str) -> bool:
    """Exempt static frontend assets and known open API paths."""
    if path in _AUTH_EXEMPT:
        return True
    # Allow all static asset requests (JS, CSS, images, fonts)
    static_exts = (".js", ".css", ".png", ".ico", ".svg", ".woff", ".woff2", ".html", ".json")
    if any(path.endswith(ext) for ext in static_exts):
        return True
    # Allow root — serves index.html
    if path == "/" or path == "":
        return True
    return False


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
    if _is_exempt(request.url.path):
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

# On HF Spaces, serve the built React frontend as static files.
# The Dockerfile copies frontend/dist into backend/static, so the
# static folder sits alongside the app/ package inside /home/appuser/backend.
if os.environ.get("HF_SPACE", "").lower() == "true":
    from fastapi.staticfiles import StaticFiles
    # Try multiple candidate paths to be resilient to working directory changes
    _candidates = [
        Path(__file__).parent.parent.parent / "static",  # backend/static from api/main.py
        Path("/home/appuser/backend/static"),             # absolute path in container
        Path("static"),                                   # relative to cwd
    ]
    _static = next((p for p in _candidates if p.exists()), None)
    if _static:
        print(f"Serving frontend from {_static}")
        app.mount("/", StaticFiles(directory=str(_static), html=True), name="static")
    else:
        print("WARNING: static folder not found, frontend will not be served")
        print("Searched:", [str(p) for p in _candidates])


@app.get("/health")
def health_check():
    """Liveness check — no auth required."""
    return {"status": "ok"}