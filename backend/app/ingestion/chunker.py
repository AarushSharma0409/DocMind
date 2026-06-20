"""
chunker.py - Phase 1, Chunk 2

WHY THIS EXISTS:
loaders.py gives us page/paragraph-level text, but a single page can be
way too large to embed meaningfully (embedding models work best on
focused chunks of a few hundred tokens, not entire pages), and way too
coarse for precise retrieval (if a page covers 3 different topics, you
don't want a query about topic 1 to retrieve the whole page, including
irrelevant topics 2 and 3).

This module splits page/paragraph-level text into smaller, token-sized
chunks, while propagating the page_number/locator_type metadata from
loaders.py into every chunk it produces. This propagation is the single
most important thing this file does - lose it, and citations break.

STRATEGY: recursive splitting with overlap.
- Try to split on paragraph breaks first (cleanest semantic boundary)
- Fall back to sentence breaks if a paragraph is still too large
- Fall back to raw token-count splitting only as a last resort (e.g. a
  single sentence longer than the chunk size, which is rare but possible)
- Consecutive chunks share a small overlap of tokens, so a sentence that
  falls right at a chunk boundary still has full context in at least
  one of the two chunks it spans.

We measure size in TOKENS, not characters, because that's what actually
maps to the embedding model's input limit - a chunk that's "2000
characters" could be 300 tokens or 600 tokens depending on the text,
but a chunk that's "500 tokens" is always close to the model's real
processing unit.
"""

import re
import tiktoken

# cl100k_base is the encoding used by GPT-4/3.5 and is a reasonable,
# widely-available approximation for token counting even when the final
# embedding model differs slightly - exact token counts aren't critical
# here, what matters is staying safely under the model's context limit.
#
# NOTE: tiktoken downloads its encoding file from OpenAI's servers on
# first use and caches it locally. This means count_tokens() can fail
# in network-restricted environments (some Docker build steps, certain
# CI runners, sandboxed environments). We fall back to a rough
# characters-per-token estimate (~4 chars/token is a standard rule of
# thumb for English text) rather than crashing the whole pipeline over
# a token-counting utility - chunking can proceed with an approximation,
# it doesn't need to be exact, it just needs to keep chunks roughly
# within the embedding model's limit.
try:
    _ENCODING = tiktoken.get_encoding("cl100k_base")
    _TIKTOKEN_AVAILABLE = True
except Exception:
    _ENCODING = None
    _TIKTOKEN_AVAILABLE = False

CHUNK_SIZE_TOKENS = 500
CHUNK_OVERLAP_TOKENS = 75


def count_tokens(text: str) -> int:
    """
    Return the number of tokens in a string.

    Uses tiktoken's cl100k_base encoding when available. Falls back to a
    characters-per-token approximation (~4 chars/token) if tiktoken's
    encoding file couldn't be loaded (e.g. no network access on first run).
    The approximation is intentionally conservative-ish; being slightly
    wrong here just means chunks are a bit smaller or larger than the
    target, not broken.
    """
    if _TIKTOKEN_AVAILABLE:
        return len(_ENCODING.encode(text))
    return max(1, len(text) // 4)


def split_into_paragraphs(text: str) -> list[str]:
    """
    Split text on blank-line boundaries (paragraph breaks).

    WHY: paragraph breaks are the cleanest semantic boundary available -
    splitting here is far less likely to cut a thought in half than
    splitting on a fixed character/token count blindly.
    """
    # Split on one or more blank lines; filter out empty strings that
    # result from multiple consecutive blank lines.
    paragraphs = re.split(r"\n\s*\n", text)
    return [p.strip() for p in paragraphs if p.strip()]


def split_into_sentences(text: str) -> list[str]:
    """
    Split text into sentences, as a fallback for paragraphs that are
    individually too large to fit in one chunk.

    WHY THIS EXISTS: most paragraphs fit comfortably under
    CHUNK_SIZE_TOKENS, but dense technical writing, long bullet-style
    paragraphs, or unusually formatted documents (no paragraph breaks at
    all) can produce a single "paragraph" longer than our chunk size. If
    we just truncated it blindly, we'd lose content or cut a sentence in
    half. Splitting by sentence first gives us a finer-grained boundary
    to pack chunks with, before resorting to raw token slicing.

    This is intentionally a simple heuristic (split after '.', '!', '?'
    followed by whitespace), not a full NLP sentence tokenizer - that
    level of precision isn't worth the added dependency for this use case.
    """
    # Split after sentence-ending punctuation followed by whitespace,
    # but keep the punctuation attached to the sentence it ends.
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return [s.strip() for s in sentences if s.strip()]


def chunk_text(text: str) -> list[str]:
    """
    Split a single block of text (e.g. one page's worth) into a list of
    token-sized chunks with overlap between consecutive chunks.

    HOW IT WORKS:
    1. Split the text into paragraphs.
    2. Greedily pack paragraphs into a chunk, one at a time, until adding
       the next paragraph would push the chunk over CHUNK_SIZE_TOKENS.
    3. When a chunk is full, finalize it and start the next chunk by
       carrying over the last CHUNK_OVERLAP_TOKENS worth of text from the
       end of the chunk just finished - this is what prevents content
       sitting right at a chunk boundary from losing context entirely.
    4. If a single paragraph is itself larger than CHUNK_SIZE_TOKENS
       (rare), fall back to splitting that paragraph by sentence and
       pack sentences the same way.

    Returns a flat list of chunk strings - metadata (page_number,
    locator_type) is NOT attached here, that happens one level up in
    chunk_document(), since this function only knows about raw text.
    """
    paragraphs = split_into_paragraphs(text)

    # Pre-process: any paragraph too large on its own gets pre-split into
    # sentences, so the packing loop below only ever deals with units
    # that individually fit within CHUNK_SIZE_TOKENS (sentences are
    # assumed to virtually always be smaller than a 500-token chunk;
    # if even a single sentence exceeds that, it gets used as one
    # oversized chunk on its own - accepting a slight overflow is better
    # than corrupting the text with a mid-word cut).
    units: list[str] = []
    for para in paragraphs:
        if count_tokens(para) <= CHUNK_SIZE_TOKENS:
            units.append(para)
        else:
            units.extend(split_into_sentences(para))

    chunks: list[str] = []
    current_chunk_units: list[str] = []
    current_chunk_tokens = 0

    for unit in units:
        unit_tokens = count_tokens(unit)

        # If adding this unit would overflow the current chunk, finalize
        # the current chunk first (unless it's still empty - a single
        # oversized unit just becomes its own chunk).
        if current_chunk_units and current_chunk_tokens + unit_tokens > CHUNK_SIZE_TOKENS:
            chunks.append(" ".join(current_chunk_units))

            # Build the overlap: walk backward through the units we just
            # finalized, collecting whole units until we've gathered
            # roughly CHUNK_OVERLAP_TOKENS worth of text. We carry whole
            # units (not partial ones) to avoid starting the new chunk
            # mid-sentence.
            overlap_units: list[str] = []
            overlap_tokens = 0
            for prev_unit in reversed(current_chunk_units):
                prev_tokens = count_tokens(prev_unit)
                if overlap_tokens + prev_tokens > CHUNK_OVERLAP_TOKENS and overlap_units:
                    break
                overlap_units.insert(0, prev_unit)
                overlap_tokens += prev_tokens

            current_chunk_units = overlap_units
            current_chunk_tokens = overlap_tokens

        current_chunk_units.append(unit)
        current_chunk_tokens += unit_tokens

    # Don't forget the final chunk being built when the loop ends.
    if current_chunk_units:
        chunks.append(" ".join(current_chunk_units))

    return chunks


def chunk_document(loaded_pages: list[dict], source_file: str | None = None) -> list[dict]:
    """
    Take loader output (from load_pdf or load_docx) and produce final,
    embedding-ready chunks with metadata propagated through.

    Input shape (from loaders.py):
        [{"page_number": 1, "locator_type": "page", "text": "..."}, ...]

    Output shape:
        [
            {
                "chunk_id": 0,
                "page_number": 1,
                "locator_type": "page",
                "source_file": "report.pdf",
                "text": "..."
            },
            ...
        ]

    WHY page_number/locator_type are propagated into EVERY chunk: this is
    the entire point of carrying this metadata through the pipeline. A
    single page might produce 2-3 chunks - each one still needs to know
    which page it came from, or citations break the moment a page is
    long enough to need splitting.

    WHY source_file MATTERS NOW (added when building ChromaDB storage):
    a single ChromaDB collection will hold chunks from MULTIPLE uploaded
    documents at once. "page_number: 4" is ambiguous across documents -
    page 4 of WHICH file? Without source_file, two different uploaded
    PDFs could both produce a chunk claiming "page 4," and there'd be no
    way to tell them apart once stored. This is the same class of
    traceability problem that locator_type solved for PDF vs. DOCX -
    citation metadata is only useful if it's unambiguous.

    source_file defaults to None so existing single-document callers and
    tests aren't forced to pass it, but the real ingestion pipeline
    (Phase 1 -> ChromaDB) always supplies it.

    chunk_id is a simple running index across the whole document (not
    per-page), useful as a stable identifier once chunks are stored in
    ChromaDB. Note: chunk_id alone is only unique WITHIN one document's
    chunk_document() call - uniqueness across the whole knowledge base
    (multiple documents) is handled by combining it with source_file when
    generating ChromaDB record IDs, not by chunk_id alone.
    """
    all_chunks: list[dict] = []
    chunk_id = 0

    for page in loaded_pages:
        page_chunks = chunk_text(page["text"])
        for chunk_str in page_chunks:
            all_chunks.append({
                "chunk_id": chunk_id,
                "page_number": page["page_number"],
                "locator_type": page["locator_type"],
                "source_file": source_file,
                "text": chunk_str,
            })
            chunk_id += 1

    return all_chunks


if __name__ == "__main__":
    # Quick manual test - confirms token counting and paragraph splitting
    # work before we build the harder part (the actual chunk-with-overlap
    # logic) on top of them.
    sample = (
        "This is the first paragraph. It has a couple of sentences.\n\n"
        "This is the second paragraph, separated by a blank line.\n\n"
        "And a third one here, just to confirm splitting works correctly."
    )

    print("--- Token count test ---")
    print(f"Sample text token count: {count_tokens(sample)}")

    print("\n--- Paragraph split test ---")
    paras = split_into_paragraphs(sample)
    print(f"Found {len(paras)} paragraphs:")
    for i, p in enumerate(paras, 1):
        print(f"  {i}. {p!r} ({count_tokens(p)} tokens)")

    print("\n--- Full chunk_document test ---")
    fake_loaded_pages = [
        {"page_number": 1, "locator_type": "page", "text": sample},
        {"page_number": 2, "locator_type": "page", "text": "A short second page."},
    ]
    result = chunk_document(fake_loaded_pages)
    print(f"Produced {len(result)} chunks from {len(fake_loaded_pages)} pages:")
    for c in result:
        print(f"  chunk_id={c['chunk_id']} page={c['page_number']} "
              f"({count_tokens(c['text'])} tokens): {c['text'][:60]!r}...")