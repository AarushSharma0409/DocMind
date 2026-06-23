"""
main.py — Phase 3, API entry point

Creates the FastAPI app instance and wires everything together.
Routers for each endpoint group live in separate files and are
registered here — this file stays thin, it's just the assembly point.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import documents, query

app = FastAPI(
    title="DocMind API",
    description="Multi-document RAG system with citations and confidence signaling.",
    version="0.1.0",
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
