"""
embedder.py - Phase 1, Chunk 3 (Embedding generation)

WHY THIS EXISTS:
chunker.py gives us text chunks with citation metadata attached, but text
alone can't be searched by meaning - only by exact keyword matching. An
embedding model converts each chunk's text into a vector (a list of
numbers) that captures its semantic meaning, so later we can compare a
user's question to chunk vectors using similarity math (cosine
similarity) instead of literal text matching. This is what makes "what
did the report say about churn" find a chunk that talks about "customer
attrition" even though the words don't match exactly.

MODEL CHOICE: all-MiniLM-L6-v2 (via sentence-transformers), run locally.
- Free, runs entirely on your own machine, no per-call cost or API key
  needed during development, where you'll be re-embedding test documents
  constantly while debugging.
- Small (~80MB) and fast relative to larger embedding models, while still
  being a well-regarded, commonly-used default for exactly this kind of
  RAG project - not a toy choice, a genuine standard one.
- Produces 384-dimensional vectors - smaller than some hosted models
  (e.g. OpenAI's text-embedding-3-small is 1536-dim), which is a real
  quality/size tradeoff worth being able to explain: a smaller embedding
  space means slightly less nuance captured per chunk, in exchange for
  speed and zero cost. Swappable later for a hosted model if the
  portfolio story calls for it - this module isolates that decision so
  switching providers later doesn't require touching chunker.py or
  anything upstream.

DESIGN: the model is loaded once (lazily, on first use) and reused for
all subsequent calls, rather than reloading it per-chunk or per-document
- reloading a transformer model is expensive (real seconds, not
milliseconds), so this matters for anything beyond a single test run.

A REAL DEPLOYMENT EDGE CASE TO KNOW ABOUT: sentence-transformers
downloads the model weights from HuggingFace's hub on first use and
caches them locally (similar to how tiktoken downloads its encoding
file - see chunker.py). This means model loading can fail in
network-restricted environments. Unlike tiktoken's token-counting
fallback, there isn't a meaningful "approximate embedding" fallback that
makes sense here - an embedding IS the model's output, there's no cheap
substitute. So instead of silently degrading, embed_chunks() fails
loudly with a clear error message explaining what to do about it
(pre-download the model, or check network access) - silently returning
garbage vectors would be far worse than a clear failure, since it would
corrupt retrieval quality without any visible symptom.
"""

from sentence_transformers import SentenceTransformer

MODEL_NAME = "all-MiniLM-L6-v2"
EMBEDDING_DIMENSION = 384  # fixed by the model choice above

_model = None  # lazy-loaded singleton, see get_model()


def get_model() -> SentenceTransformer:
    """
    Load (or return the already-loaded) embedding model.

    Lazy singleton pattern: the model is only loaded the first time it's
    actually needed, and reused after that, since loading a transformer
    model is expensive and there's no reason to pay that cost more than
    once per process.
    """
    global _model
    if _model is None:
        try:
            _model = SentenceTransformer(MODEL_NAME)
        except Exception as e:
            raise RuntimeError(
                f"Failed to load embedding model '{MODEL_NAME}'. "
                f"This usually means no network access to download the "
                f"model on first use (it's cached locally after that). "
                f"If you're in a restricted environment (Docker build, "
                f"CI, offline sandbox), pre-download the model with "
                f"network access first, or check your connection. "
                f"Original error: {e}"
            ) from e
    return _model


def embed_chunks(chunks: list[dict]) -> list[dict]:
    """
    Take chunked output (from chunker.py's chunk_document) and attach an
    embedding vector to every chunk.

    Input shape (from chunker.py):
        [{"chunk_id": 0, "page_number": 1, "locator_type": "page", "text": "..."}, ...]

    Output shape:
        [{"chunk_id": 0, "page_number": 1, "locator_type": "page",
          "text": "...", "embedding": [0.123, -0.045, ...]}, ...]

    WHY BATCHED, NOT ONE CALL PER CHUNK: encoding all chunk texts in a
    single model.encode() call lets sentence-transformers process them
    together (internally batched on whatever hardware is available),
    which is meaningfully faster than looping and calling encode() once
    per chunk - this matters more as document count grows, and it's a
    detail worth being able to explain ("I batch embeddings instead of
    making N separate model calls").

    Chunks with empty/whitespace-only text are skipped entirely (same
    defensive principle used throughout loaders.py and chunker.py) rather
    than embedding an empty string, which would produce a meaningless
    vector that could falsely match unrelated queries.
    """
    if not chunks:
        return []

    # Filter out any chunk with empty text before embedding - defensive,
    # since upstream code (chunker.py) shouldn't produce these, but this
    # function shouldn't silently embed garbage if it ever happens.
    valid_chunks = [c for c in chunks if c["text"] and c["text"].strip()]

    if not valid_chunks:
        return []

    model = get_model()
    texts = [c["text"] for c in valid_chunks]

    # show_progress_bar=False keeps output clean for programmatic use;
    # convert_to_numpy=True (the default) gives us arrays we then convert
    # to plain Python lists, since ChromaDB (next step) expects standard
    # lists, not numpy arrays, when storing embeddings.
    vectors = model.encode(texts, show_progress_bar=False)

    embedded_chunks = []
    for chunk, vector in zip(valid_chunks, vectors):
        embedded_chunks.append({
            **chunk,
            "embedding": vector.tolist(),
        })

    return embedded_chunks


if __name__ == "__main__":
    # Quick manual test - confirms the model loads and produces vectors
    # of the expected shape. Requires network access on first run to
    # download the model (subsequent runs use the local cache).
    sample_chunks = [
        {"chunk_id": 0, "page_number": 1, "locator_type": "page",
         "text": "The quarterly report showed a decline in customer retention."},
        {"chunk_id": 1, "page_number": 1, "locator_type": "page",
         "text": "Revenue grew by twelve percent compared to last year."},
    ]

    print(f"Embedding {len(sample_chunks)} sample chunks with {MODEL_NAME}...")
    result = embed_chunks(sample_chunks)

    print(f"Produced {len(result)} embedded chunks")
    for c in result:
        print(f"  chunk_id={c['chunk_id']}: embedding length = {len(c['embedding'])} "
              f"(expected {EMBEDDING_DIMENSION})")
        assert len(c["embedding"]) == EMBEDDING_DIMENSION, "Embedding dimension mismatch!"

    print("\nPASS: all embeddings have the expected dimension")
