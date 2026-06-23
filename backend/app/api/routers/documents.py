"""
documents.py — Phase 3, document endpoints

POST /documents/upload  — accepts a PDF or DOCX file, runs the full
                          ingestion pipeline, stores chunks in ChromaDB
GET  /documents/        — lists all unique source files in ChromaDB

WHY THESE TWO ENDPOINTS TOGETHER:
The upload endpoint is the entry point to the entire ingestion pipeline.
The list endpoint lets the frontend (and you, during development) verify
what's actually been stored — without it, the store is a black box.
"""

import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import JSONResponse

from app.ingestion.loaders import load_pdf, load_docx
from app.ingestion.chunker import chunk_document
from app.ingestion.embedder import embed_chunks
from app.storage.vector_store import (
    store_chunks,
    delete_by_source_file,
    get_collection,
    DEFAULT_COLLECTION_NAME,
)

router = APIRouter(prefix="/documents", tags=["documents"])

# Supported file types — anything else is rejected at the boundary,
# before any pipeline work is done.
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt"}

# ChromaDB config — these two values must be consistent everywhere:
# upload, query, and list all need to point at the same store.
# Path is resolved relative to this file so it works regardless of
# which directory uvicorn is launched from.
PERSIST_DIR = str(Path(__file__).resolve().parent.parent.parent.parent / "chroma_store")
COLLECTION_NAME = DEFAULT_COLLECTION_NAME


@router.post("/upload")
async def upload_document(file: UploadFile = File(...)):
    """
    Accepts a PDF or DOCX file, runs the full ingestion pipeline, and
    stores the resulting chunks in ChromaDB.

    If the same filename was uploaded before, its existing chunks are
    deleted first — re-upload always replaces, never duplicates.
    """
    # --- 1. Validate file type up front ---
    suffix = Path(file.filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type '{suffix}'. Accepted: {sorted(SUPPORTED_EXTENSIONS)}",
        )

    tmp_path = None
    try:
        # --- 2. Write upload to a temp file so loaders can read from disk ---
        # loaders.py expects a file path, not a bytes stream.
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            contents = await file.read()
            tmp.write(contents)
            tmp_path = tmp.name

        # --- 3. Load ---
        if suffix == ".pdf":
            pages = load_pdf(tmp_path)
        elif suffix == ".docx":
            pages = load_docx(tmp_path)
        else:
            # .txt — no dedicated loader needed. Read the whole file as one
            # "page" in the same shape load_pdf/load_docx return, so the
            # rest of the pipeline (chunker, embedder) needs no changes.
            text = Path(tmp_path).read_text(encoding="utf-8", errors="replace")
            pages = [{"page_number": 1, "locator_type": "page", "text": text}]

        if not pages:
            raise HTTPException(
                status_code=422,
                detail="File was parsed but contained no extractable text.",
            )

        # --- 4. Chunk ---
        chunks = chunk_document(pages, source_file=file.filename)

        if not chunks:
            raise HTTPException(
                status_code=422,
                detail="Document was loaded but produced no chunks after splitting.",
            )

        # --- 5. Embed ---
        embedded = embed_chunks(chunks)

        # --- 6. Store — delete existing chunks first to avoid duplicates ---
        delete_by_source_file(file.filename, COLLECTION_NAME, PERSIST_DIR)
        stored = store_chunks(embedded, COLLECTION_NAME, PERSIST_DIR)

    except HTTPException:
        raise  # let our own HTTPExceptions pass through untouched
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ingestion failed: {str(e)}",
        )
    finally:
        # Always clean up the temp file, even if ingestion failed
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    return JSONResponse(
        status_code=200,
        content={
            "message": "Document ingested successfully.",
            "filename": file.filename,
            "chunks_stored": stored,
        },
    )


@router.get("/")
def list_documents():
    """
    Returns a list of all unique source files currently stored in ChromaDB.

    Useful for the frontend to show what documents are available to query,
    and during development to verify uploads are landing correctly.
    """
    try:
        collection = get_collection(COLLECTION_NAME, PERSIST_DIR)
        result = collection.get(include=["metadatas"])
        metadatas = result.get("metadatas") or []

        # Extract unique source_file values, preserving insertion order
        seen = set()
        documents = []
        for meta in metadatas:
            source = meta.get("source_file", "unknown")
            if source not in seen:
                seen.add(source)
                documents.append(source)

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve document list: {str(e)}",
        )

    return {"documents": documents, "count": len(documents)}