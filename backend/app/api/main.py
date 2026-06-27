"""
main.py — Phase 3, API entry point

Creates the FastAPI app instance and wires everything together.
Routers for each endpoint group live in separate files and are
registered here — this file stays thin, it's just the assembly point.

STARTUP OPTIMIZATION: the embedding model (all-MiniLM-L6-v2) is
eager-loaded here via FastAPI's lifespan hook, rather than lazily on
the first upload request. Without this, the first upload after starting
the server pays a 2–5 second model-load tax on top of ingestion time,
making it feel broken. With eager loading, uvicorn prints "Model ready"
before accepting requests, and every subsequent upload skips that cost
entirely.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import documents, query
from app.ingestion.embedder import get_model


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Runs once at startup (before any requests), and once at shutdown.

    We use it to warm the embedding model — loading all-MiniLM-L6-v2
    takes 2–5 seconds on first call (reading weights from disk into
    memory). Doing it here means:
      - uvicorn is 'ready' only after the model is loaded
      - the first upload request doesn't pay this cost
      - every upload from that point on uses the already-loaded singleton

    This is the correct pattern for any expensive resource (model, DB
    connection pool, etc.) that needs to be ready before the first
    request hits.
    """
    print("⏳ Loading embedding model...")
    get_model()  # warms the singleton in embedder.py
    print("✅ Model ready — accepting requests")
    yield
    # anything after yield runs on shutdown (nothing to clean up here)


app = FastAPI(
    title="DocMind API",
    description="Multi-document RAG system with citations and confidence signaling.",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — allows the React frontend (running on a different port during
# development) to make requests to this backend.
# In production, replace ["*"] with your actual frontend domain.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers — each file handles one concern
app.include_router(documents.router)
app.include_router(query.router)


@app.get("/health")
def health_check():
    """Quick liveness check — confirms the API is running."""
    return {"status": "ok"}