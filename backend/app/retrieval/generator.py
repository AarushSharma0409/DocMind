"""
generator.py - Phase 2, Chunk 3 (Generation & Citation Extraction)

WHY THIS EXISTS:
retriever.py (Chunk 1) returns the most relevant chunks for a query, but
raw chunks aren't useful to an end user on their own - they need a
coherent, synthesized answer. This module takes the query plus retrieved
chunks, sends them to Gemini with a structured prompt, and returns a
clean response containing both the answer and traceable citations back
to the exact chunks the answer actually used.

WHY GEMINI, CONSISTENTLY WITH query_router.py: this project's LLM layer
uses Gemini throughout (see query_router.py for the original reasoning -
working credentials were already set up for Gemini, not Anthropic). An
earlier draft of this module was built against Groq instead, which would
have meant maintaining two different LLM providers, two SDKs, and two
auth setups for two halves of one pipeline - a real inconsistency, not
just a style preference. That Groq-based draft is kept in the repo as a
documented fallback option (see generator_groq_backup.py) in case Gemini
access changes, but Gemini is the primary implementation.

A REAL BUG AVOIDED BY BUILDING AGAINST THE ACTUAL retriever.py CONTRACT:
an earlier draft of this module assumed retrieved chunks have a
"chunk_id" field. retriever.py's actual return shape does NOT include
chunk_id - it returns {"text", "source_file", "page_number",
"locator_type", "similarity"}. This module builds citation identity from
source_file + page_number instead (which is what's actually available
and what actually matters for showing a user "you can verify this on
page 4 of report.pdf"), rather than assuming a field that doesn't exist
in the real pipeline.

WHY LLM-SIDE CITATION, NOT POST-PROCESSING: matching generated sentences
back to source chunks via post-processing is brittle - paraphrased
answers won't match chunk text verbatim, and the matching logic becomes
its own engineering problem. Constraining the model (via the prompt and
schema) to only cite chunks we explicitly provide, by index into the
provided chunk list, prevents hallucinated sources structurally rather
than trying to detect hallucination after the fact.

WHY NATIVE STRUCTURED OUTPUT (response_schema), SAME AS query_router.py:
removes an entire class of parsing failure (markdown-fenced JSON,
commentary wrapping) that prompt-only JSON requests are vulnerable to -
see query_router.py's module docstring for the full reasoning, which
applies identically here.

FALLBACK BEHAVIOR - WHY THIS DIFFERS FROM query_router.py'S SILENT
FALLBACK: query_router.py silently falls back to a safe default route on
any failure, because routing wrong (still doing a search) degrades
gracefully. Generation is different - if the LLM call genuinely fails,
there is no safe "default answer" to silently substitute; pretending an
answer was generated when it wasn't would be actively misleading to the
user. So generate() raises a single, clearly-named exception
(GenerationError) on failure, rather than five different exception types
for different failure modes (the earlier Groq-based draft's approach) -
callers need to handle ONE failure case for "generation didn't work",
not enumerate every possible underlying cause.

OUTPUT SHAPE:
{
    "answer": "...",
    "citations": [
        {
            "source_file": "report.pdf",
            "page_number": 4,
            "locator_type": "page",
            "excerpt": "..."
        }
    ]
}
"""

from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

# Load backend/.env regardless of which directory this script is run
# from. load_dotenv() with no arguments searches upward from the
# CURRENT WORKING DIRECTORY, which is unreliable - e.g. running this
# file directly from backend/app/retrieval/ vs. running it as
# `python -m app.retrieval.generator` from backend/ are two different
# working directories, and the bare load_dotenv() call found .env in
# one case but not the other. Resolving the path explicitly, relative
# to THIS FILE's location (not the working directory), makes loading
# .env work the same way no matter how the script is invoked.
_ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH)

GENERATION_MODEL = "gemini-2.5-flash"
ANSWER_MODE_QA = "qa"
ANSWER_MODE_SUMMARY = "summary"
VALID_ANSWER_MODES = (ANSWER_MODE_QA, ANSWER_MODE_SUMMARY)
MAX_CHUNKS_IN_PROMPT = 5  # matches retriever.py's DEFAULT_TOP_K - kept as
                           # one source of truth, see note in generate()
MAX_SUMMARY_CHUNKS_IN_PROMPT = 25

GENERATION_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "citations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    # Index into the chunk list passed in the prompt -
                    # NOT a chunk_id field, since retriever.py's output
                    # doesn't have one. The model cites WHICH of the
                    # chunks we gave it supported a claim, by position.
                    "chunk_index": {"type": "integer"},
                    "excerpt": {"type": "string"},
                },
                "required": ["chunk_index", "excerpt"],
            },
        },
    },
    "required": ["answer", "citations"],
}

_client = None  # lazy singleton, same pattern as query_router.py/embedder.py


class GenerationError(Exception):
    """
    Raised when answer generation fails for any reason (API error,
    network failure, unparseable response). A single exception type
    rather than several, since callers need to handle ONE failure case -
    "generation didn't work" - not enumerate every possible underlying
    cause. See module docstring's FALLBACK BEHAVIOR section for why this
    differs from query_router.py's silent-fallback approach.
    """
    pass


def get_client() -> "genai.Client":
    """Lazily create (and reuse) the Gemini API client. Same pattern as query_router.py."""
    global _client
    if _client is None:
        _client = genai.Client()
    return _client


def _build_generation_prompt(query: str,
                             chunks: list[dict],
                             answer_mode: str = ANSWER_MODE_QA) -> str:
    """
    Build the generation prompt from the query and retrieved chunks.

    Each chunk is presented with an explicit index (0-based, matching its
    position in the `chunks` list) so the model can cite "chunk_index: 2"
    rather than needing a chunk_id field that retriever.py doesn't
    provide. The prompt explicitly forbids citing an index outside the
    provided range.
    """
    chunk_blocks = []
    for i, chunk in enumerate(chunks):
        block = (
            f"[CHUNK {i}]\n"
            f"source_file: {chunk.get('source_file', 'unknown')}\n"
            f"page_number: {chunk.get('page_number', 'unknown')}\n"
            f"text: {chunk.get('text', '')}"
        )
        chunk_blocks.append(block)

    chunks_text = "\n\n".join(chunk_blocks)

    shared_rules = f"""Use ONLY the information in the chunks below.
Do not use outside knowledge.
If the chunks do not contain enough information, say that plainly.
Write in a natural, helpful style for a human reader, not like a keyword matcher.
Synthesize related details across chunks instead of listing fragments.
For every factual point, cite the supporting chunk using chunk_index.
Only use chunk_index values from 0 to {len(chunks) - 1}. Never invent an index outside this range.
For each citation, copy the most relevant sentence or phrase from that chunk verbatim as the excerpt."""

    if answer_mode == ANSWER_MODE_SUMMARY:
        task_rules = """The user is asking for a document-level summary or overview.
Do not answer by searching for only the words in the query.
Cover the document broadly using the provided chunks in document order.
Make the answer easy to read with short sections:
- A brief overall summary
- Key points
- Important details, numbers, decisions, or findings
- Any limitations or missing context visible from the provided chunks
If multiple documents are present, separate the summary by document.
Avoid generic filler. Prefer concrete details from the document."""
    else:
        task_rules = """The user is asking a specific question about the documents.
Start with the direct answer, then give the supporting details.
If the answer is nuanced, explain the nuance in plain language.
If several chunks disagree or describe different parts of the answer, reconcile them clearly.
Do not simply repeat matching sentences unless that is the clearest answer."""

    return f"""You are DocMind's document reading assistant.

TASK MODE:
{answer_mode}

RULES:
{shared_rules}

MODE-SPECIFIC INSTRUCTIONS:
{task_rules}

USER QUERY:
{query}

CHUNKS:
{chunks_text}"""


def generate(query: str,
             chunks: list[dict],
             answer_mode: str = ANSWER_MODE_QA) -> dict:
    """
    Generate an answer with citations for the given query and retrieved
    chunks (as returned by retriever.py's retrieve()).

    Args:
        query:  The user's original query string.
        chunks: Retrieved chunks from retriever.py. Each must have at
                minimum: text, source_file, page_number, locator_type.

    Returns:
        {"answer": str, "citations": [{"source_file": str,
         "page_number": int, "locator_type": str, "excerpt": str}, ...]}

    Raises:
        ValueError:       Empty query or no chunks provided.
        GenerationError:  Any failure - API error, network issue,
                           unparseable response. See module docstring's
                           FALLBACK BEHAVIOR section for why this is a
                           single exception type, and why generation
                           raises rather than silently falling back
                           (unlike query_router.py).
    """
    if not query or not query.strip():
        raise ValueError("Query must be a non-empty string.")
    if not chunks:
        raise ValueError("At least one chunk must be provided for generation.")
    if answer_mode not in VALID_ANSWER_MODES:
        raise ValueError(f"answer_mode must be one of: {', '.join(VALID_ANSWER_MODES)}")

    # Normal QA uses a small, focused context from similarity retrieval.
    # Summaries intentionally allow broader context because their job is
    # to describe the document, not just answer from the nearest chunks.
    max_chunks = (
        MAX_SUMMARY_CHUNKS_IN_PROMPT
        if answer_mode == ANSWER_MODE_SUMMARY
        else MAX_CHUNKS_IN_PROMPT
    )
    capped_chunks = chunks[:max_chunks]

    try:
        client = get_client()
        prompt = _build_generation_prompt(query, capped_chunks, answer_mode=answer_mode)

        response = client.models.generate_content(
            model=GENERATION_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=GENERATION_RESPONSE_SCHEMA,
            ),
        )

        return _parse_generation_response(response.text, capped_chunks)

    except (ValueError,):
        raise  # let input-validation errors above propagate as-is
    except Exception as e:
        raise GenerationError(f"Answer generation failed: {e}") from e


def _parse_generation_response(raw_text: str, chunks: list[dict]) -> dict:
    """
    Parse and validate the model's JSON response, converting
    chunk_index-based citations into full citation records using the
    actual chunk metadata (source_file, page_number, locator_type).

    WHY VALIDATION, EVEN WITH A SCHEMA-CONSTRAINED RESPONSE: the schema
    guarantees shape (citations is a list of {chunk_index, excerpt}), but
    it can't guarantee chunk_index is actually within range - the model
    could still output an out-of-bounds index. This function defends
    against that (the hallucination-equivalent risk for this module,
    same principle as query_router.py's hallucinated-filename defense)
    by silently dropping any citation with an invalid index rather than
    crashing the whole response over one bad citation.
    """
    import json

    try:
        parsed = json.loads(raw_text)
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        raise GenerationError(f"Model response was not valid JSON: {e}") from e

    if "answer" not in parsed or not isinstance(parsed["answer"], str):
        raise GenerationError("Response missing a valid 'answer' string field.")
    if "citations" not in parsed or not isinstance(parsed["citations"], list):
        raise GenerationError("Response missing a valid 'citations' list field.")

    full_citations = []
    for citation in parsed["citations"]:
        if not isinstance(citation, dict):
            continue  # skip malformed citation entries rather than failing the whole response

        chunk_index = citation.get("chunk_index")
        excerpt = citation.get("excerpt")

        # Defend against an out-of-range or non-integer chunk_index -
        # the equivalent of query_router.py's hallucinated-filename
        # guard, applied here to citation indices instead of filenames.
        if not isinstance(chunk_index, int) or not (0 <= chunk_index < len(chunks)):
            continue

        source_chunk = chunks[chunk_index]
        full_citations.append({
            "source_file": source_chunk.get("source_file"),
            "page_number": source_chunk.get("page_number"),
            "locator_type": source_chunk.get("locator_type"),
            "excerpt": excerpt if isinstance(excerpt, str) else "",
        })

    return {
        "answer": parsed["answer"],
        "citations": full_citations,
    }


if __name__ == "__main__":
    # Quick manual test - requires a real GEMINI_API_KEY in the
    # environment. Uses fake chunks shaped exactly like retriever.py's
    # real output (no chunk_id field), to confirm this module matches
    # the actual pipeline contract.
    fake_chunks = [
        {
            "text": "The company reported a net revenue of $4.2 billion in fiscal year 2023, "
                    "representing a 12% year-over-year growth driven by expansion in APAC markets.",
            "source_file": "annual_report.pdf",
            "page_number": 2,
            "locator_type": "page",
            "similarity": 0.91,
        },
        {
            "text": "Operating expenses increased by 8% primarily due to headcount growth "
                    "in the engineering and sales divisions.",
            "source_file": "annual_report.pdf",
            "page_number": 3,
            "locator_type": "page",
            "similarity": 0.84,
        },
    ]

    query = "What was the revenue growth and what drove it?"

    print(f"Query: {query}\n")
    print("Running generator...\n")

    result = generate(query, fake_chunks)

    print("Answer:", result["answer"])
    print("\nCitations:")
    for c in result["citations"]:
        print(f"  - [{c['source_file']} | p.{c['page_number']} | {c['locator_type']}]")
        print(f"    \"{c['excerpt']}\"")
