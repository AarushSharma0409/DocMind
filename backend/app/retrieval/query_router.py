"""
query_router.py - Phase 2, Chunk 2 (Query routing)

WHY THIS EXISTS:
retriever.py (Chunk 1) always does a vector similarity search - but not
every query actually needs one. "Summarize document 2" doesn't need
top-5 similar CHUNKS, it needs the whole document, retrieving only 5
chunks would badly under-serve a summarization request and produce an
incomplete summary. "Hello" or "what can you help me with" doesn't need
ANY document retrieval at all. Blindly vector-searching every query
regardless of what's actually being asked is the naive version of RAG -
this module is what makes DocMind's retrieval strategy-aware rather than
one-size-fits-all, which is one of the project's stated differentiators.

WHY LLM-BASED CLASSIFICATION, NOT KEYWORD RULES: keyword matching (e.g.
looking for "summarize" or "overview") is fast and free but brittle - it
misses natural phrasings it wasn't explicitly built for ("give me the
gist of report.pdf" wouldn't match a rule for "summarize"). An LLM
classification call handles natural language robustly.

WHY GEMINI: this project uses the Gemini API (via google-genai) for the
routing classification step, with a fast, free-tier-friendly model
suited to a simple, low-stakes classification task that doesn't need
top-tier model quality.

WHY NATIVE STRUCTURED OUTPUT, NOT PROMPT-ENGINEERED JSON: the Gemini SDK
supports response_mime_type="application/json" plus a response_schema,
which makes the model's output GUARANTEED to match the given schema -
this is more reliable than asking nicely in a prompt for "ONLY a JSON
object" and hoping the model doesn't wrap it in markdown fences or add
commentary, which is a common failure mode with prompt-only JSON
requests. Using the SDK's native structured-output feature removes an
entire class of parsing failure.

THE THREE ROUTES:
- "retrieve": the default/most common case - a specific question that
  needs similarity search over chunks (e.g. "what did the report say
  about churn")
- "full_document": the query is asking to summarize/overview a SPECIFIC
  named document, not search for a specific fact within it - needs the
  whole document's content, not a handful of top-k chunks
- "no_retrieval": general chat/meta questions that don't need any
  document context at all (e.g. "hello", "what can you do")

FALLBACK BEHAVIOR: if the API call fails (network issue, rate limit,
missing/invalid API key) or returns something unparseable, route_query()
defaults to "retrieve" rather than raising - this is the safest
fallback, since "just do a similarity search" degrades gracefully
(worst case: returns generic chunks, not a crash) while still attempting
to answer the question, which is better than the whole query failing
over a routing-layer issue.
"""

from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

# Load backend/.env regardless of which directory this script is run
# from. A bare load_dotenv() searches upward from the CURRENT WORKING
# DIRECTORY, which is unreliable - this exact issue was found while
# testing generator.py (worked when run as a module from backend/, but
# failed when run directly from a subdirectory). Resolving the path
# explicitly relative to THIS FILE's location, rather than the working
# directory, makes .env loading work the same way no matter how the
# script is invoked.
_ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH)

ROUTER_MODEL = "gemini-2.5-flash"

VALID_ROUTES = ("retrieve", "full_document", "no_retrieval")
DEFAULT_FALLBACK_ROUTE = "retrieve"

# Sentinel string used in place of JSON null for target_document - see
# ROUTE_RESPONSE_SCHEMA comment below for why this is needed instead of
# a nullable type.
NO_TARGET_DOCUMENT_SENTINEL = "none"

# JSON schema the model's response is constrained to match exactly -
# see WHY NATIVE STRUCTURED OUTPUT above for why this is more reliable
# than prompt-only JSON requests.
#
# A REAL BUG FOUND AND FIXED: target_document was originally declared as
# "type": ["string", "null"] (standard JSON Schema syntax for a nullable
# field). Gemini's response_schema does NOT support type arrays/unions -
# it only accepts a single type per field (STRING, NUMBER, OBJECT, etc.),
# confirmed by a real validation error: "Input should be
# 'TYPE_UNSPECIFIED', 'STRING', 'NUMBER', 'INTEGER', 'BOOLEAN', 'ARRAY',
# 'OBJECT' or 'NULL' [type=enum, input_value=['string', 'null']]". This
# caused EVERY classification call to fail at the API level, silently
# falling back to the default route - the routing logic itself was never
# actually being exercised. Fixed by declaring target_document as a
# plain "string" and having the model use an explicit sentinel value
# (NO_TARGET_DOCUMENT_SENTINEL) instead of JSON null when there's no
# target document - the sentinel is converted back to Python None during
# parsing (_parse_routing_response), so callers never see the sentinel
# leak through.
ROUTE_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "route": {
            "type": "string",
            "enum": list(VALID_ROUTES),
        },
        "target_document": {
            "type": "string",
        },
    },
    "required": ["route", "target_document"],
}

_client = None  # lazy singleton, mirrors the pattern used in embedder.py/vector_store.py


def get_client() -> "genai.Client":
    """
    Lazily create (and reuse) the Gemini API client.

    WHY LAZY: avoids requiring an API key to be present just to IMPORT
    this module (e.g. for tests that mock route_query entirely) - the
    key is only needed at actual call time, not at import time.

    Reads GEMINI_API_KEY (or GOOGLE_API_KEY) from the environment
    automatically via genai.Client().
    """
    global _client
    if _client is None:
        _client = genai.Client()
    return _client


def _build_classification_prompt(query: str, available_documents: list[str]) -> str:
    """
    Build the prompt sent to the routing model.

    Includes available_documents so the model can recognize when a query
    references a SPECIFIC document by name (relevant for full_document
    routing) - without this list, the model would have no way to know
    "report.pdf" is a real, known document versus a name the user made up.

    Instructs the model to use NO_TARGET_DOCUMENT_SENTINEL instead of
    JSON null for "no document referenced" - see ROUTE_RESPONSE_SCHEMA's
    comment for why null/nullable types aren't usable with Gemini's
    response_schema.
    """
    doc_list = ", ".join(available_documents) if available_documents else "(no documents uploaded yet)"

    return f"""Classify the following user query into EXACTLY ONE of these three categories:

1. "retrieve" - The query asks a specific question that needs searching
   through document content for relevant information (e.g. "what did the
   report say about churn", "find mentions of the Q3 budget").

2. "full_document" - The query asks to summarize, overview, or get the
   gist of a SPECIFIC named document as a whole, not search for one fact
   within it (e.g. "summarize report.pdf", "give me an overview of the
   second document").

3. "no_retrieval" - The query is general chat, a greeting, or a question
   about DocMind itself that doesn't require looking at any uploaded
   document content (e.g. "hello", "what can you help me with").

Available documents: {doc_list}

User query: "{query}"

Set "target_document" to the specific filename from the available
documents list if the query clearly refers to one (relevant mainly for
full_document routing). If no specific document is referenced, set
"target_document" to exactly the string "{NO_TARGET_DOCUMENT_SENTINEL}"
(not empty string, not the word "null" - use this exact word)."""


def route_query(query: str, available_documents: list[str] | None = None) -> dict:
    """
    Classify a user query into one of three routes.

    Returns:
        {"route": "retrieve" | "full_document" | "no_retrieval",
         "target_document": str | None}

    Falls back to {"route": "retrieve", "target_document": None} if the
    query is empty, if the API call fails, or if the response can't be
    parsed - see module docstring for why "retrieve" is the safe default.
    """
    if not query or not query.strip():
        return {"route": DEFAULT_FALLBACK_ROUTE, "target_document": None}

    available_documents = available_documents or []

    try:
        client = get_client()
        prompt = _build_classification_prompt(query, available_documents)

        response = client.models.generate_content(
            model=ROUTER_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=ROUTE_RESPONSE_SCHEMA,
            ),
        )

        return _parse_routing_response(response.text, available_documents)

    except Exception:
        # Any failure (network, API error, missing/invalid API key,
        # malformed response) falls back to the safe default rather than
        # crashing the whole query - see module docstring's FALLBACK
        # BEHAVIOR section for reasoning.
        return {"route": DEFAULT_FALLBACK_ROUTE, "target_document": None}


def _parse_routing_response(raw_text: str, available_documents: list[str]) -> dict:
    """
    Parse and validate the model's JSON response.

    WHY VALIDATION, EVEN WITH A SCHEMA-CONSTRAINED RESPONSE: the schema
    guarantees shape (route is one of the enum values, target_document
    is always a string per ROUTE_RESPONSE_SCHEMA), but it can't guarantee
    target_document is a REAL filename rather than a hallucinated one the
    model invented, or that it isn't the "no document" sentinel that
    needs converting back to Python None. This function defends against
    both cases rather than trusting the model's output blindly, since a
    routing decision downstream code acts on should never be built on an
    unvalidated assumption.
    """
    import json

    try:
        parsed = json.loads(raw_text)
    except (json.JSONDecodeError, ValueError, TypeError):
        return {"route": DEFAULT_FALLBACK_ROUTE, "target_document": None}

    route = parsed.get("route")
    if route not in VALID_ROUTES:
        return {"route": DEFAULT_FALLBACK_ROUTE, "target_document": None}

    target_document = parsed.get("target_document")

    # Convert the "no document" sentinel back to Python None - see
    # ROUTE_RESPONSE_SCHEMA's comment for why a sentinel is used instead
    # of JSON null (Gemini's response_schema doesn't support nullable
    # types).
    if target_document == NO_TARGET_DOCUMENT_SENTINEL:
        target_document = None

    # Defend against a hallucinated filename that isn't actually in the
    # known document list - treat it as None rather than trusting it,
    # since downstream code (full_document retrieval) would otherwise
    # try to look up a document that was never actually uploaded.
    if target_document is not None and target_document not in available_documents:
        target_document = None

    return {"route": route, "target_document": target_document}


if __name__ == "__main__":
    # Quick manual test - requires a real GEMINI_API_KEY in the
    # environment to actually call the API. Tests three queries that
    # should clearly land in each of the three different routes.
    test_documents = ["quarterly_report.pdf", "meeting_notes.docx"]

    test_cases = [
        ("What did the quarterly report say about customer churn?", "retrieve"),
        ("Summarize quarterly_report.pdf for me", "full_document"),
        ("Hello, what can you help me with?", "no_retrieval"),
    ]

    print(f"Testing query routing against: {test_documents}\n")

    for query, expected_route in test_cases:
        result = route_query(query, available_documents=test_documents)
        status = "PASS" if result["route"] == expected_route else "MISMATCH"
        print(f"[{status}] Query: {query!r}")
        print(f"         Expected: {expected_route}, Got: {result['route']}, "
              f"target_document: {result['target_document']}")
        print()
