"""
vector_store.py - Phase 1, Chunk 4 (ChromaDB persistent storage)

WHY THIS EXISTS:
embedder.py produces chunks with vectors attached, but those vectors only
exist in memory until something stores them durably and makes them
searchable by similarity. This module is that something: it wraps
ChromaDB to store embedded chunks persistently (surviving app restarts)
and to run similarity search against them later, in Phase 2.

WHY CHROMADB: it's a vector database that handles the similarity-search
math (cosine distance between vectors) internally, so retrieval code in
Phase 2 doesn't need to implement that itself. It supports a persistent
on-disk mode, which is what we use here - re-embedding every document on
every app restart would be wasteful and slow.

WHY A PERSISTENT CLIENT, NOT IN-MEMORY: an in-memory ChromaDB client
loses all stored data the moment the Python process ends. That's fine
for a quick test script, but wrong for a real application - a user
shouldn't have to re-upload and re-embed every document every time the
backend restarts. PersistentClient writes to disk at a configured path,
so the knowledge base survives restarts.

A NOTE ON IDs: ChromaDB requires a unique string ID for every stored
record. chunk_document() in chunker.py produces a chunk_id that's only
unique WITHIN one document - if you embed two different files, both
could have chunks with chunk_id=0. This module builds the actual
ChromaDB record ID by combining source_file and chunk_id
(f"{source_file}::{chunk_id}"), which is unique across the whole
knowledge base. This is the same class of problem source_file was added
to chunker.py to solve - citation/storage metadata is only useful if
it's unambiguous across multiple documents, not just within one.

A NOTE ON METADATA TYPES: ChromaDB's metadata fields only accept str,
int, float, or bool - not None, and not arbitrary nested structures.
Since chunker.py's source_file defaults to None for backward
compatibility, this module explicitly converts None to the string
"unknown" before storing, so a None value never gets silently rejected
or causes a storage error at write time.
"""

import chromadb
from chromadb.config import Settings

DEFAULT_PERSIST_DIR = "./data/chroma"
DEFAULT_COLLECTION_NAME = "docmind_chunks"

# Cache of clients keyed by persist_dir, NOT a single global singleton.
#
# WHY A DICT, NOT ONE SHARED CLIENT: a naive single-client singleton (the
# first version of this function) silently broke if called with two
# different persist_dir values in the same process - the first call's
# client would be cached and returned for every later call regardless of
# what persist_dir was actually requested, pointing at the wrong data
# entirely with no error. This matters in practice: the test suite needs
# an isolated temp directory per test, and a real app could legitimately
# want multiple collections (e.g. one per user, or a separate test/dev
# database) - both cases need the cache keyed by the actual path
# requested, not a single shared instance.
_clients: dict[str, "chromadb.ClientAPI"] = {}


def get_client(persist_dir: str = DEFAULT_PERSIST_DIR):
    """
    Return a persistent ChromaDB client for the given persist_dir,
    creating it on first call for that path and reusing it after that.

    Caching is keyed by persist_dir specifically - calling this with two
    different paths correctly returns two different clients, rather than
    silently reusing whichever client happened to be created first (see
    the module-level comment on _clients for why this matters).
    """
    if persist_dir not in _clients:
        _clients[persist_dir] = chromadb.PersistentClient(
            path=persist_dir,
            settings=Settings(anonymized_telemetry=False),
        )
    return _clients[persist_dir]


def get_collection(collection_name: str = DEFAULT_COLLECTION_NAME,
                    persist_dir: str = DEFAULT_PERSIST_DIR):
    """
    Get (or create, if it doesn't exist yet) the ChromaDB collection
    DocMind stores chunks in. get_or_create_collection is idempotent -
    safe to call every time the app starts, won't wipe existing data.

    WHY hnsw:space="cosine" IS EXPLICITLY SET: this is a real bug found
    during Phase 2 development. ChromaDB's default distance metric, if
    not explicitly configured, is SQUARED L2 (squared Euclidean)
    distance, not cosine distance - confirmed by direct testing (an
    orthogonal vector pair returned distance=2.0, and an opposite vector
    pair returned distance=4.0, which only matches squared L2, not
    cosine). This matters because retriever.py's similarity score
    conversion (1 - distance) is only mathematically valid for cosine
    distance, which has a bounded, predictable range. Without this
    explicit setting, every computed "similarity" score was wrong
    (returning near-zero for genuinely strong matches), even though
    RESULT ORDERING still happened to be correct - which is why the bug
    wasn't obvious from ranking alone and only showed up once actual
    similarity scores were inspected. Cosine distance is also the
    standard choice for text embeddings specifically (it measures
    directional similarity, ignoring vector magnitude, which fits how
    sentence-transformers models are designed to be compared).
    """
    client = get_client(persist_dir)
    return client.get_or_create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine"},
    )


def _build_record_id(chunk: dict) -> str:
    """
    Build a ChromaDB record ID that's unique across the WHOLE knowledge
    base, not just within one document.

    WHY: chunk_id alone repeats across documents (every document's first
    chunk has chunk_id=0). Combining source_file and chunk_id makes the
    combination unique, as long as source_file is actually distinct per
    document (which it should be - see chunker.py's source_file param).
    """
    source = chunk.get("source_file") or "unknown"
    return f"{source}::{chunk['chunk_id']}"


def _sanitize_metadata(chunk: dict) -> dict:
    """
    Build the metadata dict ChromaDB will store alongside each vector.

    WHY THIS EXISTS: ChromaDB's metadata values must be str, int, float,
    or bool - None is not accepted and will raise an error at write
    time. chunker.py's source_file can legitimately be None (its
    documented default for backward compatibility), so we convert that
    to the string "unknown" here rather than let storage fail on a
    perfectly valid (if incomplete) chunk.

    Note: "text" and "embedding" are NOT included here - ChromaDB stores
    the embedding vector separately (as the actual vector being indexed)
    and the chunk text separately (as the "document" field), not as
    metadata. Metadata is for the citation fields used to identify WHERE
    a chunk came from.
    """
    return {
        "chunk_id": chunk["chunk_id"],
        "page_number": chunk["page_number"],
        "locator_type": chunk["locator_type"],
        "source_file": chunk.get("source_file") or "unknown",
    }


def store_chunks(embedded_chunks: list[dict],
                  collection_name: str = DEFAULT_COLLECTION_NAME,
                  persist_dir: str = DEFAULT_PERSIST_DIR) -> int:
    """
    Persist a list of embedded chunks (output of embedder.py's
    embed_chunks) into ChromaDB.

    Input shape (from embedder.py):
        [{"chunk_id": 0, "page_number": 1, "locator_type": "page",
          "source_file": "report.pdf", "text": "...",
          "embedding": [0.123, ...]}, ...]

    Returns the number of chunks actually stored (0 for empty input,
    rather than erroring on nothing to do).

    WHY BATCHED: collection.add() accepts lists for ids/embeddings/
    documents/metadatas and stores them all in one call - this is
    meaningfully faster than calling add() once per chunk, same batching
    principle used in embedder.py's model.encode() call.

    WHY upsert-safe (uses add, not raising on duplicates silently): if
    the same document is re-ingested (e.g. the user re-uploads a file
    they already added), record IDs will collide since they're built
    from source_file + chunk_id. ChromaDB's add() raises on duplicate
    IDs by default - callers who want re-ingestion to overwrite existing
    data should delete the old document's chunks first (see
    delete_by_source_file) rather than relying on add() to silently
    handle it.
    """
    if not embedded_chunks:
        return 0

    collection = get_collection(collection_name, persist_dir)

    ids = [_build_record_id(c) for c in embedded_chunks]
    embeddings = [c["embedding"] for c in embedded_chunks]
    documents = [c["text"] for c in embedded_chunks]
    metadatas = [_sanitize_metadata(c) for c in embedded_chunks]

    collection.add(
        ids=ids,
        embeddings=embeddings,
        documents=documents,
        metadatas=metadatas,
    )

    return len(embedded_chunks)


def delete_by_source_file(source_file: str,
                           collection_name: str = DEFAULT_COLLECTION_NAME,
                           persist_dir: str = DEFAULT_PERSIST_DIR) -> None:
    """
    Remove all chunks belonging to a given source file from the
    collection. Used to support re-ingesting a document cleanly (delete
    the old version's chunks before storing the new version), rather
    than accumulating duplicate/stale chunks from repeated uploads of
    the same file.
    """
    collection = get_collection(collection_name, persist_dir)
    collection.delete(where={"source_file": source_file})


def delete_all_chunks(collection_name: str = DEFAULT_COLLECTION_NAME,
                      persist_dir: str = DEFAULT_PERSIST_DIR) -> int:
    """
    Remove every stored chunk from the collection.

    ChromaDB delete() is safest when called with explicit IDs, so this
    fetches the current IDs first and deletes exactly those records.
    Returns the number of chunks requested for deletion so API callers
    can report what was cleared.
    """
    collection = get_collection(collection_name, persist_dir)
    result = collection.get()
    ids = result.get("ids") or []

    if not ids:
        return 0

    collection.delete(ids=ids)
    return len(ids)


def count_chunks(collection_name: str = DEFAULT_COLLECTION_NAME,
                  persist_dir: str = DEFAULT_PERSIST_DIR) -> int:
    """Return the total number of chunks currently stored in the collection."""
    collection = get_collection(collection_name, persist_dir)
    return collection.count()


if __name__ == "__main__":
    # Quick manual test - uses a temporary collection so repeated runs
    # don't accumulate stale data, and exercises store -> count -> delete
    # to confirm the basic lifecycle works end to end.
    import tempfile
    import shutil

    test_dir = tempfile.mkdtemp()
    print(f"Using temporary ChromaDB directory: {test_dir}")

    try:
        fake_embedded_chunks = [
            {
                "chunk_id": 0, "page_number": 1, "locator_type": "page",
                "source_file": "test_doc.pdf", "text": "First chunk of text.",
                "embedding": [0.1] * 384,
            },
            {
                "chunk_id": 1, "page_number": 1, "locator_type": "page",
                "source_file": "test_doc.pdf", "text": "Second chunk of text.",
                "embedding": [0.2] * 384,
            },
        ]

        print("\n--- Storing chunks ---")
        stored_count = store_chunks(fake_embedded_chunks, persist_dir=test_dir)
        print(f"Stored {stored_count} chunks")

        print("\n--- Counting chunks ---")
        total = count_chunks(persist_dir=test_dir)
        print(f"Collection now has {total} chunks")
        assert total == 2, f"Expected 2 chunks, found {total}"

        print("\n--- Deleting by source_file ---")
        delete_by_source_file("test_doc.pdf", persist_dir=test_dir)
        total_after_delete = count_chunks(persist_dir=test_dir)
        print(f"Collection now has {total_after_delete} chunks after deletion")
        assert total_after_delete == 0, f"Expected 0 chunks after delete, found {total_after_delete}"

        print("\nPASS: store -> count -> delete lifecycle works correctly")

    finally:
        shutil.rmtree(test_dir, ignore_errors=True)
        print(f"\nCleaned up temporary directory: {test_dir}")
