"""
retriever.py - Phase 2, Chunk 1 (Basic similarity retrieval)

WHY THIS EXISTS:
Phase 1 built the pipeline to get documents INTO ChromaDB as searchable
vectors. This module is the first piece of getting information back OUT:
given a user's question, find the chunks most likely to contain the
answer. This is the "R" in RAG (Retrieval-Augmented Generation) - the
LLM's eventual answer (Phase 2, Chunk 3) will only be as good as what
this function retrieves.

HOW IT WORKS:
1. Embed the user's query using the SAME model used to embed documents
   (all-MiniLM-L6-v2, via embedder.py) - query and document vectors only
   mean anything relative to each other if they're produced by the same
   model, in the same vector space.
2. Ask ChromaDB for the N stored chunks whose vectors are most similar
   to the query vector (cosine similarity, which ChromaDB computes
   internally).
3. Return those chunks in a shape that's immediately useful for citation
   (Phase 2, Chunk 3) - text, source_file, page_number, locator_type,
   and a similarity score so the confidence-signaling layer (Chunk 4)
   has something to evaluate.

WHY TOP 5 BY DEFAULT: a tradeoff between precision and recall. Top 3 is
tighter but riskier - with a smaller local embedding model (384-dim,
not the largest available), the single most relevant chunk could
plausibly rank 4th rather than 1st-3rd due to imperfect semantic
matching, and a too-narrow result set could miss it entirely. Top 5
gives the LLM enough surrounding context to synthesize a good answer,
and gives the confidence-signaling layer more signal to work with - "all
5 results have low similarity" is a more reliable weak-retrieval signal
than "all 3 results have low similarity." This is configurable
(top_k parameter), not hardcoded, since the right number is something
to tune once real retrieval quality is observed on real documents.

WHY DISTANCE, NOT JUST "SIMILARITY": ChromaDB's query() returns a
DISTANCE score (lower = more similar), not a 0-1 similarity score
directly. This module converts distance to a more intuitive similarity
score (1 - distance, clamped to [0, 1]) before returning results, so
downstream code (and a human reading retrieval results while debugging)
doesn't have to remember "lower number = better match."

A REAL BUG FOUND AND FIXED: this conversion is only mathematically valid
for COSINE distance specifically. ChromaDB's actual default distance
metric, if not explicitly configured, is squared L2 (Euclidean)
distance, NOT cosine - confirmed by direct testing (an orthogonal vector
pair returned distance=2.0, an opposite pair returned distance=4.0,
which only matches squared L2, not cosine's expected 1.0/2.0). This was
caught when every computed similarity score came back as 0.0 even for a
genuinely strong semantic match, despite RESULT ORDERING still being
correct - which is why it wasn't obvious from ranking alone. The fix
lives in vector_store.py's get_collection(), which now explicitly sets
metadata={"hnsw:space": "cosine"} when creating the collection, making
this module's 1 - distance conversion valid again.
"""

from app.ingestion.embedder import get_model
from app.storage.vector_store import get_collection, DEFAULT_COLLECTION_NAME, DEFAULT_PERSIST_DIR

DEFAULT_TOP_K = 5
DEFAULT_FULL_DOCUMENT_MAX_CHUNKS = 25


def retrieve(query: str,
             top_k: int = DEFAULT_TOP_K,
             collection_name: str = DEFAULT_COLLECTION_NAME,
             persist_dir: str = DEFAULT_PERSIST_DIR) -> list[dict]:
    """
    Embed a user's query and return the top_k most similar stored chunks.

    Returns a list of dicts shaped for direct use in citation and
    generation:
        [
            {
                "text": "...",
                "source_file": "report.pdf",
                "page_number": 4,
                "locator_type": "page",
                "similarity": 0.83,
            },
            ...
        ]

    Results are ordered from most to least similar (ChromaDB's default
    ordering for a similarity query).

    Returns an empty list if the query is empty/whitespace-only, or if
    the collection has no stored chunks yet - both are valid states, not
    errors, so callers shouldn't need a try/except for "no results."
    """
    if not query or not query.strip():
        return []

    collection = get_collection(collection_name, persist_dir)

    if collection.count() == 0:
        return []

    # Cap top_k at however many chunks actually exist - asking ChromaDB
    # for more results than exist in the collection is handled gracefully
    # by ChromaDB itself, but being explicit here avoids relying on that
    # implicit behavior and makes the actual constraint visible in code.
    effective_top_k = min(top_k, collection.count())

    model = get_model()
    query_embedding = model.encode([query], show_progress_bar=False)[0].tolist()

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=effective_top_k,
    )

    return _format_results(results)


def retrieve_document(source_file: str | None = None,
                      max_chunks: int = DEFAULT_FULL_DOCUMENT_MAX_CHUNKS,
                      collection_name: str = DEFAULT_COLLECTION_NAME,
                      persist_dir: str = DEFAULT_PERSIST_DIR) -> list[dict]:
    """
    Return document chunks directly, without similarity-searching by the
    user's query.

    This is the retrieval path for whole-document tasks like "summarize
    the PDF I uploaded". A vector query can only return the chunks most
    semantically similar to the words in the prompt, which is exactly
    the wrong shape for summaries: a summary needs broad coverage across
    the document, in document order.
    """
    if max_chunks <= 0:
        return []

    collection = get_collection(collection_name, persist_dir)
    if collection.count() == 0:
        return []

    get_kwargs = {"include": ["documents", "metadatas"]}
    if source_file:
        get_kwargs["where"] = {"source_file": source_file}

    results = collection.get(**get_kwargs)
    documents = results.get("documents") or []
    metadatas = results.get("metadatas") or []

    chunks = []
    for text, metadata in zip(documents, metadatas):
        chunks.append({
            "text": text,
            "source_file": metadata.get("source_file", "unknown"),
            "page_number": metadata.get("page_number", 0),
            "locator_type": metadata.get("locator_type", "page"),
            "chunk_id": metadata.get("chunk_id", 0),
            # Full-document retrieval is intentionally not similarity
            # based. Use a neutral high score so the existing confidence
            # layer does not misread broad context as weak vector search.
            "similarity": 1.0,
        })

    chunks.sort(key=lambda c: (
        str(c.get("source_file", "")),
        c.get("page_number", 0),
        c.get("chunk_id", 0),
    ))

    return chunks[:max_chunks]


def _format_results(chroma_results: dict) -> list[dict]:
    """
    Convert ChromaDB's raw query() response shape into the flat,
    citation-ready list this module returns.

    ChromaDB's query() returns results nested one level deeper than you'd
    expect - documents/metadatas/distances are each a list of lists (one
    inner list per query embedding submitted). Since retrieve() only ever
    submits ONE query embedding at a time, we always want index [0] of
    each outer list. This helper isolates that unwrapping in one place
    rather than repeating the [0] indexing logic inline.
    """
    documents = chroma_results["documents"][0]
    metadatas = chroma_results["metadatas"][0]
    distances = chroma_results["distances"][0]

    formatted = []
    for text, metadata, distance in zip(documents, metadatas, distances):
        # ChromaDB's default distance metric is cosine distance, where
        # lower = more similar. Converting to a similarity score
        # (1 - distance) is more intuitive for anything downstream that
        # reasons about "how confident is this match" - see
        # confidence-signaling, Phase 2 Chunk 4. Clamped to [0, 1] since
        # cosine distance can technically exceed 1 in edge cases, and a
        # similarity score outside [0, 1] would be confusing to consumers.
        similarity = max(0.0, min(1.0, 1 - distance))

        formatted.append({
            "text": text,
            "source_file": metadata["source_file"],
            "page_number": metadata["page_number"],
            "locator_type": metadata["locator_type"],
            "similarity": similarity,
        })

    return formatted


if __name__ == "__main__":
    # Quick manual test - stores a few fake chunks with KNOWN topics,
    # then runs a query that should clearly match one of them over the
    # others, to sanity check that retrieval actually returns sensible
    # results, not just results.
    import tempfile
    import shutil
    from app.ingestion.embedder import embed_chunks
    from app.storage.vector_store import store_chunks

    test_dir = tempfile.mkdtemp()
    print(f"Using temporary ChromaDB directory: {test_dir}")

    try:
        fake_chunks = [
            {"chunk_id": 0, "page_number": 1, "locator_type": "page",
             "source_file": "finance_report.pdf",
             "text": "Quarterly revenue grew by twelve percent compared to last year."},
            {"chunk_id": 1, "page_number": 1, "locator_type": "page",
             "source_file": "finance_report.pdf",
             "text": "Customer churn increased significantly in the last quarter, "
                      "raising concerns among the leadership team."},
            {"chunk_id": 2, "page_number": 1, "locator_type": "page",
             "source_file": "recipe_book.pdf",
             "text": "Preheat the oven to 350 degrees and grease the baking pan."},
        ]

        print("\n--- Embedding and storing test chunks ---")
        embedded = embed_chunks(fake_chunks)
        store_chunks(embedded, persist_dir=test_dir)
        print(f"Stored {len(embedded)} chunks")

        print("\n--- Querying: 'What did the report say about customer attrition?' ---")
        results = retrieve(
            "What did the report say about customer attrition?",
            persist_dir=test_dir,
        )

        for i, r in enumerate(results, 1):
            print(f"  {i}. [similarity={r['similarity']:.3f}] "
                  f"{r['source_file']} p.{r['page_number']}: {r['text'][:60]}...")

        assert len(results) == 3, f"Expected 3 results (collection has 3 chunks), got {len(results)}"
        top_result = results[0]
        assert "churn" in top_result["text"].lower(), (
            f"Expected the churn-related chunk to rank first for a query about "
            f"'customer attrition' (a near-synonym), but top result was: "
            f"{top_result['text']!r}"
        )
        print("\nPASS: query about 'customer attrition' correctly retrieved the "
              "chunk about 'churn' as the top result, despite no exact word match - "
              "this confirms semantic (not just keyword) search is working.")

    finally:
        shutil.rmtree(test_dir, ignore_errors=True)
        print(f"\nCleaned up temporary directory: {test_dir}")
