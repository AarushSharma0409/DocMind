"""
query.py — Phase 3, query endpoint

POST /query — takes a user query, runs it through the full Phase 2
              pipeline, and returns an answer with citations and a
              confidence signal.

PIPELINE ORDER:
1. route_query()      — decide HOW to handle the query
2. retrieve()         — get relevant chunks from ChromaDB
3. assess_confidence()— judge retrieval quality BEFORE generation,
                        so the signal survives even if generation fails
4. generate()         — synthesize answer with citations

WHY CONFIDENCE BEFORE GENERATION:
If generate() raises GenerationError, the API still needs to tell the
user something meaningful. "Retrieval was weak" is useful information
even when generation failed — it explains why. Assessing confidence
first means that signal is never lost to a downstream failure.
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.retrieval.query_router import route_query
from app.retrieval.retriever import (
    retrieve,
    retrieve_document,
    DEFAULT_COLLECTION_NAME,
    DEFAULT_PERSIST_DIR,
)
from app.retrieval.generator import generate, GenerationError, ANSWER_MODE_QA, ANSWER_MODE_SUMMARY
from app.retrieval.confidence import assess_confidence
from app.storage.vector_store import get_collection

router = APIRouter(prefix="/query", tags=["query"])

# Must match documents.py — same store, same collection.
PERSIST_DIR = str(Path(__file__).resolve().parent.parent.parent.parent / "chroma_store")
COLLECTION_NAME = DEFAULT_COLLECTION_NAME


class QueryRequest(BaseModel):
    query: str


@router.post("/")
def query_documents(request: QueryRequest):
    """
    Run a query through the full RAG pipeline.

    Returns:
        {
            "query":      str,
            "route":      "retrieve" | "full_document" | "no_retrieval",
            "answer":     str,
            "citations":  [{"source_file", "page_number", "locator_type", "excerpt"}],
            "confidence": {"level": "high"|"medium"|"low", "reason": str}
        }
    """
    query = request.query.strip()
    if not query:
        raise HTTPException(status_code=422, detail="Query must not be empty.")

    # --- 1. Get list of available documents for the router ---
    # route_query needs this so it can recognize "summarize report.pdf"
    # as a full_document request for a real, known file.
    try:
        collection = get_collection(COLLECTION_NAME, PERSIST_DIR)
        meta_result = collection.get(include=["metadatas"])
        metadatas = meta_result.get("metadatas") or []
        seen = set()
        available_docs = []
        for m in metadatas:
            src = m.get("source_file", "unknown")
            if src not in seen:
                seen.add(src)
                available_docs.append(src)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to read document list: {str(e)}",
        )

    # --- 2. Route ---
    routing = route_query(query, available_documents=available_docs)
    route = routing["route"]

    # --- 3. Handle no_retrieval route ---
    # No document context needed — answer directly without retrieval.
    if route == "no_retrieval":
        return {
            "query": query,
            "route": route,
            "answer": "This question doesn't require searching your documents. "
                      "Please ask something specific about your uploaded files.",
            "citations": [],
            "confidence": {"level": "low", "reason": "No retrieval was performed."},
        }

    # --- 4. Retrieve ---
    try:
        if route == "full_document":
            # If the user says "summarize the PDF I uploaded" and only
            # one document exists, treat that as the target document.
            # With multiple documents and no explicit target, summarize
            # across all uploaded documents instead of falling back to a
            # keyword-style vector search.
            target_document = routing.get("target_document")
            if target_document is None and len(available_docs) == 1:
                target_document = available_docs[0]

            chunks = retrieve_document(
                target_document,
                collection_name=COLLECTION_NAME,
                persist_dir=PERSIST_DIR,
            )
            answer_mode = ANSWER_MODE_SUMMARY
        else:
            chunks = retrieve(query, collection_name=COLLECTION_NAME, persist_dir=PERSIST_DIR)
            answer_mode = ANSWER_MODE_QA
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Retrieval failed: {str(e)}",
        )

    if not chunks:
        return {
            "query": query,
            "route": route,
            "answer": "No relevant content was found in your uploaded documents.",
            "citations": [],
            "confidence": {
                "level": "low",
                "reason": "No chunks were retrieved. The documents may not contain "
                          "information about this query.",
            },
        }

    # --- 5. Assess confidence BEFORE generation ---
    confidence = assess_confidence(chunks)

    # --- 6. Generate ---
    try:
        generation = generate(query, chunks, answer_mode=answer_mode)
    except GenerationError as e:
        # Generation failed — still return confidence so the user knows
        # whether retrieval was the problem or something else.
        raise HTTPException(
            status_code=500,
            detail={
                "error": f"Answer generation failed: {str(e)}",
                "confidence": confidence,
            },
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return {
        "query": query,
        "route": route,
        "answer": generation["answer"],
        "citations": generation["citations"],
        "confidence": confidence,
    }
