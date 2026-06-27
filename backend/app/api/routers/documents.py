"""
documents.py — Phase 3, document endpoints

POST /documents/upload  — accepts a PDF, DOCX, or TXT file, immediately
                          returns 202 Accepted, then runs ingestion in
                          the background
GET  /documents/        — lists all unique source files in ChromaDB
DELETE /documents/{filename} — removes one document's chunks
DELETE /documents/      — removes all chunks

INGESTION SPEED OPTIMIZATION:
The original design ran the full pipeline (load → chunk → embed → store)
synchronously inside the upload request, so the user's browser sat
waiting through all of it — typically 3–8 seconds for a normal PDF.
The bottleneck is model.encode() in embedder.py, which is CPU-bound and
can't be made meaningfully faster without changing the model.

The fix: FastAPI's BackgroundTasks. The upload endpoint now:
  1. Validates the file type and reads the bytes — fast, stays sync
  2. Returns 202 Accepted immediately — the browser unblocks
  3. Runs the pipeline in a background task after the response is sent

The frontend already polls GET /documents/ to show "Indexing..." vs
"Indexed" status, so this fits the existing UI contract perfectly — the
document appears as "Indexing…" instantly, then transitions to "Indexed"
once the background task finishes, typically a few seconds later.

WHY BackgroundTasks AND NOT asyncio / threading DIRECTLY:
FastAPI's BackgroundTasks runs after the response is sent, in the same
process, using Starlette's built-in task runner. It's the right tool
for "do this after responding" — simpler than spinning up a thread pool
or a task queue (Celery, RQ) for a workload this size. The tradeoff is
that the task shares the process with request handlers, so a very
slow ingestion (large scanned PDF with OCR) could theoretically slow
down concurrent requests. For a portfolio project that won't see
concurrent heavy ingestion, this is the correct call. A production
system with many simultaneous users would want a proper task queue.
"""

import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, UploadFile, File
from fastapi.responses import JSONResponse

from app.ingestion.loaders import load_pdf, load_docx
from app.ingestion.chunker import chunk_document
from app.ingestion.embedder import embed_chunks
from app.storage.vector_store import (
    store_chunks,
    delete_by_source_file,
    delete_all_chunks,
    get_collection,
    DEFAULT_COLLECTION_NAME,
)

router = APIRouter(prefix="/documents", tags=["documents"])

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt"}

PERSIST_DIR = str(Path(__file__).resolve().parent.parent.parent.parent / "chroma_store")
COLLECTION_NAME = DEFAULT_COLLECTION_NAME


def _run_ingestion(tmp_path: str, suffix: str, filename: str) -> None:
    """
    Full ingestion pipeline, intended to run as a background task.

    Reads from a temp file written by the upload endpoint, runs
    load → chunk → embed → store, then cleans up the temp file.
    Errors are logged but not raised — a background task can't send an
    HTTP response, so there's no way to surface failures to the caller
    after 202 has already been sent. The document simply won't appear
    as "Indexed" in the list endpoint if ingestion fails.

    NOTE: for a production system, you'd want to write failure state
    somewhere the frontend can poll (e.g. a status endpoint or a DB
    record), so users know when ingestion actually failed. For this
    portfolio project, the tradeoff is accepted — the common path works
    cleanly, and failures are visible in the server logs.
    """
    try:
        # Load
        if suffix == ".pdf":
            pages = load_pdf(tmp_path)
        elif suffix == ".docx":
            pages = load_docx(tmp_path)
        else:
            text = Path(tmp_path).read_text(encoding="utf-8", errors="replace")
            pages = [{"page_number": 1, "locator_type": "page", "text": text}]

        if not pages:
            print(f"[ingestion] {filename}: no extractable text, skipping")
            return

        # Chunk
        chunks = chunk_document(pages, source_file=filename)
        if not chunks:
            print(f"[ingestion] {filename}: produced no chunks, skipping")
            return

        # Embed — this is the slow step (CPU-bound model.encode)
        embedded = embed_chunks(chunks)

        # Store — delete existing chunks first to avoid duplicates on re-upload
        delete_by_source_file(filename, COLLECTION_NAME, PERSIST_DIR)
        stored = store_chunks(embedded, COLLECTION_NAME, PERSIST_DIR)

        print(f"[ingestion] {filename}: stored {stored} chunks ✓")

    except Exception as e:
        print(f"[ingestion] {filename}: FAILED — {e}")

    finally:
        # Always clean up the temp file
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


@router.post("/upload", status_code=202)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
):
    """
    Accept a file, validate it, then immediately return 202 and kick off
    ingestion as a background task.

    The response comes back in ~50ms (just file read + temp write).
    Actual ingestion (chunking, embedding, storage) happens after the
    response is sent and takes a few seconds in the background.

    The frontend shows "Indexing…" on the document card until GET
    /documents/ confirms the file is present in ChromaDB.
    """
    # Validate file type at the boundary — before reading anything
    suffix = Path(file.filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type '{suffix}'. Accepted: {sorted(SUPPORTED_EXTENSIONS)}",
        )

    # Write to temp file — loaders need a path, not a byte stream.
    # We can't use a context manager here because the file must persist
    # until the background task finishes reading it.
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        contents = await file.read()
        tmp.write(contents)
        tmp_path = tmp.name

    # Queue the pipeline to run after this response is sent.
    # tmp_path cleanup happens inside _run_ingestion's finally block.
    background_tasks.add_task(_run_ingestion, tmp_path, suffix, file.filename)

    return JSONResponse(
        status_code=202,
        content={
            "message": "File accepted. Ingestion running in background.",
            "filename": file.filename,
            "status": "indexing",
        },
    )


@router.get("/")
def list_documents():
    """
    Returns all unique source files currently stored in ChromaDB.

    This is also what the frontend polls to detect when a background
    ingestion has completed — a file appears here only after its chunks
    are fully stored.
    """
    try:
        collection = get_collection(COLLECTION_NAME, PERSIST_DIR)
        result = collection.get(include=["metadatas"])
        metadatas = result.get("metadatas") or []

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


@router.delete("/{source_file}")
def delete_document(source_file: str):
    """Delete all stored chunks for one source file."""
    try:
        collection = get_collection(COLLECTION_NAME, PERSIST_DIR)
        before = collection.count()
        delete_by_source_file(source_file, COLLECTION_NAME, PERSIST_DIR)
        deleted = before - collection.count()

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete document: {str(e)}",
        )

    return {
        "message": "Document deleted.",
        "filename": source_file,
        "chunks_deleted": deleted,
    }


@router.delete("/")
def clear_documents():
    """Delete every stored chunk from the vector database."""
    try:
        deleted = delete_all_chunks(COLLECTION_NAME, PERSIST_DIR)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to clear documents: {str(e)}",
        )

    return {
        "message": "All documents cleared.",
        "chunks_deleted": deleted,
    }