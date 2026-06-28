"""
documents.py — Phase 3, document endpoints

POST /documents/upload       — validate, then ingest in background (202)
GET  /documents/             — list all indexed source files
GET  /documents/status       — per-file ingestion status (indexing/indexed/failed)
DELETE /documents/{filename} — remove one document's chunks
DELETE /documents/           — remove all chunks

SECURITY FIXES (all active):

1. FILE SIZE LIMIT
   Files over MAX_UPLOAD_BYTES (25 MB) are rejected with 413 after
   reading bytes but before any disk or pipeline work. Content-Length
   headers are client-controlled and can lie — checking len(contents)
   after the read is the only reliable approach.

2. MAGIC BYTE VALIDATION
   Extension checking is trivially bypassed by renaming a file. After
   reading bytes, _check_magic_bytes() inspects the actual file header:
   - PDF:  must start with b'%PDF'
   - DOCX: must start with b'PK\x03\x04' (Office Open XML is a ZIP)
   - TXT:  must be decodable as UTF-8 (no binary content)
   A renamed .exe masquerading as .pdf fails here with 415, not inside
   the parser with a cryptic 500.

3. FILENAME SANITISATION
   file.filename comes from the multipart header — the client controls
   it entirely. _sanitise_filename() strips directory components
   (preventing path traversal) and removes shell-special characters
   before the name is used as a ChromaDB metadata key or in logs.

4. RATE LIMITING
   Upload is limited to 10/minute per IP via slowapi. The limiter
   instance is imported from main.py where it's registered with the app.

BACKGROUND FAILURE VISIBILITY:
   _ingestion_status tracks per-file state: "indexing" → "indexed" or
   "failed". GET /documents/status exposes this so the frontend can show
   a real failure state instead of leaving the user waiting indefinitely
   for a file that silently failed to ingest.
"""

import os
import re
import tempfile
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, UploadFile, File
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
from app.api.main import limiter

router = APIRouter(prefix="/documents", tags=["documents"])

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt"}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB

PERSIST_DIR = str(Path(__file__).resolve().parent.parent.parent.parent / "chroma_store")
COLLECTION_NAME = DEFAULT_COLLECTION_NAME

# Characters safe in a ChromaDB metadata value and in log output.
_SAFE_FILENAME_RE = re.compile(r"[^\w\s\-.]")

# In-memory ingestion status store.
# Keys are sanitised filenames. Values are "indexing" | "indexed" | "failed".
# Lives in process memory — resets on server restart, which is acceptable
# for a portfolio project. A production system would persist this to a DB.
IngestionStatus = Literal["indexing", "indexed", "failed"]
_ingestion_status: dict[str, IngestionStatus] = {}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sanitise_filename(raw: str) -> str:
    """
    Return a safe version of a client-supplied filename.

    Path(raw).name strips directory components ("../../etc/passwd" → "passwd").
    _SAFE_FILENAME_RE removes shell metacharacters, null bytes, and anything
    unexpected. Falls back to "unnamed_upload" if sanitisation produces
    an empty string.
    """
    name = Path(raw).name
    name = _SAFE_FILENAME_RE.sub("", name).strip()
    return name or "unnamed_upload"


def _check_magic_bytes(contents: bytes, suffix: str) -> None:
    """
    Verify file contents match the declared extension by inspecting the
    file header (magic bytes), not just the extension.

    WHY: a client can rename any file to .pdf and pass extension checking.
    Magic bytes are embedded in the file itself and can't be faked without
    also making the file parseable as that type — which defeats the attack.

    PDF:  header is b'%PDF' (25 50 44 46)
    DOCX: header is b'PK\\x03\\x04' — Office Open XML is a ZIP archive
    TXT:  no magic bytes standard exists; we verify UTF-8 decodability
          instead. A binary file masquerading as .txt will fail here.

    Raises HTTPException(415) on mismatch so the caller gets a clear
    error rather than a cryptic parser failure further down the pipeline.
    """
    if suffix == ".pdf":
        if not contents[:4] == b"%PDF":
            raise HTTPException(
                status_code=415,
                detail="File content does not match a valid PDF. "
                       "Ensure the file is not corrupted or renamed.",
            )
    elif suffix == ".docx":
        # DOCX (Office Open XML) is a ZIP file — PK\x03\x04 is the ZIP magic
        if not contents[:4] == b"PK\x03\x04":
            raise HTTPException(
                status_code=415,
                detail="File content does not match a valid DOCX. "
                       "Ensure the file is a Word document (.docx), not .doc or another format.",
            )
    elif suffix == ".txt":
        # TXT has no magic bytes — validate it's decodable as UTF-8
        try:
            contents.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(
                status_code=415,
                detail="File does not appear to be a valid UTF-8 text file. "
                       "Binary files renamed as .txt are not supported.",
            )


# ── Background ingestion ──────────────────────────────────────────────────────

def _run_ingestion(tmp_path: str, suffix: str, filename: str) -> None:
    """
    Full ingestion pipeline run as a background task after 202 is sent.

    Updates _ingestion_status[filename] to "indexed" on success or
    "failed" on any error, so GET /documents/status can surface the
    outcome to the frontend. Cleans up the temp file in all cases.
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
            _ingestion_status[filename] = "failed"
            return

        # Chunk
        chunks = chunk_document(pages, source_file=filename)
        if not chunks:
            print(f"[ingestion] {filename}: produced no chunks, skipping")
            _ingestion_status[filename] = "failed"
            return

        # Embed
        embedded = embed_chunks(chunks)

        # Store
        delete_by_source_file(filename, COLLECTION_NAME, PERSIST_DIR)
        stored = store_chunks(embedded, COLLECTION_NAME, PERSIST_DIR)

        _ingestion_status[filename] = "indexed"
        print(f"[ingestion] {filename}: stored {stored} chunks ✓")

    except Exception as e:
        _ingestion_status[filename] = "failed"
        print(f"[ingestion] {filename}: FAILED — {e}")

    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/upload", status_code=202)
@limiter.limit("10/minute")
async def upload_document(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
):
    """
    Accept a file, run security checks, then return 202 and ingest in background.

    Checks in order (cheapest first):
      1. Extension — reject unsupported types before reading bytes
      2. Read bytes into memory
      3. File size — reject over 25 MB before any disk/pipeline work
      4. Magic bytes — reject files whose content doesn't match extension
      5. Filename sanitisation — strip traversal and unsafe characters
      6. Write to temp file, queue background ingestion, return 202
    """
    # 1. Extension check
    suffix = Path(file.filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=(
                f"Unsupported file type '{suffix}'. "
                f"Accepted: {sorted(SUPPORTED_EXTENSIONS)}"
            ),
        )

    # 2. Read
    contents = await file.read()

    # 3. Size check
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"File too large ({len(contents) / 1024 / 1024:.1f} MB). "
                f"Maximum allowed size is {MAX_UPLOAD_BYTES // 1024 // 1024} MB."
            ),
        )

    # 4. Magic byte validation
    _check_magic_bytes(contents, suffix)

    # 5. Sanitise filename
    safe_filename = _sanitise_filename(file.filename)

    # 6. Write temp file and queue ingestion
    _ingestion_status[safe_filename] = "indexing"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(contents)
        tmp_path = tmp.name

    background_tasks.add_task(_run_ingestion, tmp_path, suffix, safe_filename)

    return JSONResponse(
        status_code=202,
        content={
            "message": "File accepted. Ingestion running in background.",
            "filename": safe_filename,
            "status": "indexing",
        },
    )


@router.get("/status")
def ingestion_status():
    """
    Per-file ingestion status for all files uploaded in this server session.

    Returns "indexing" | "indexed" | "failed" per filename.
    Resets on server restart (in-memory only).

    The frontend polls this alongside GET /documents/ to detect failures —
    a file stuck on "indexing" that never appears in /documents/ is a
    silent failure; this endpoint makes it visible as "failed" instead.
    """
    return {"status": dict(_ingestion_status)}


@router.get("/")
def list_documents():
    """
    Returns all unique source files currently stored in ChromaDB.

    The frontend polls this to detect when background ingestion has
    completed — a file appears here only after its chunks are fully stored.
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
        _ingestion_status.pop(source_file, None)

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
        _ingestion_status.clear()

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to clear documents: {str(e)}",
        )

    return {
        "message": "All documents cleared.",
        "chunks_deleted": deleted,
    }