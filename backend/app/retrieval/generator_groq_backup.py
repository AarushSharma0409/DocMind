"""
generator_groq_backup.py - NOT THE ACTIVE IMPLEMENTATION

This is a backup/fallback implementation of generation + citation
extraction using the Groq API (llama-3.1-8b-instant), kept for reference
in case Gemini access changes or a second provider is genuinely needed
later. The ACTIVE implementation is generator.py, which uses Gemini for
consistency with query_router.py (this project's other LLM-calling
module).

WHY THIS EXISTS AS A SEPARATE FILE RATHER THAN BEING DELETED: it
represents real, working effort (citation hallucination guarding,
structured JSON parsing, a reasonably thorough test suite) and Groq's
free tier with no billing requirement is a genuinely useful fallback to
have documented if Gemini's free tier limits ever become a blocker.
Keeping it as an explicitly-named, clearly-not-imported-by-default file
avoids the confusion of having two active LLM providers in the same
pipeline while still preserving the work and the option.

KNOWN ISSUES IN THIS VERSION (not fixed here, since it's not active):
- Assumes retrieved chunks have a "chunk_id" field, which retriever.py's
  actual output does NOT include (retriever.py returns text/source_file/
  page_number/locator_type/similarity, no chunk_id). Would need the same
  chunk_index approach generator.py uses if ever activated for real.
- MAX_CHUNKS_IN_PROMPT here is a second, independent definition of the
  same concept as retriever.py's DEFAULT_TOP_K and generator.py's own
  MAX_CHUNKS_IN_PROMPT - would need reconciling, not three separate
  constants for one limit.
- Uses several exception types (ValueError, EnvironmentError,
  GeneratorParseError, TimeoutError, RuntimeError) for what
  generator.py treats as one failure case (GenerationError) - see
  generator.py's module docstring for the reasoning on why a single
  exception type is preferred for this use case.

TO ACTIVATE THIS INSTEAD OF generator.py:
1. Fix the chunk_id assumption (see KNOWN ISSUES above)
2. pip install groq
3. Set GROQ_API_KEY in backend/.env
4. Update any code importing from generator.py to import from this file
   instead - and update docs/ARCHITECTURE.md to reflect the provider
   change, the same way the Anthropic -> Gemini switch was documented.

---

ORIGINAL MODULE DOCSTRING (Groq version):

WHY GROQ FOR GENERATION:
Groq provides a free tier with no billing required, and hosts
llama-3.1-8b-instant which handles structured JSON output reliably.
The Groq SDK is OpenAI-compatible, making the interface clean and familiar.

WHY LLM-SIDE CITATION (not post-processing):
Post-processing (matching generated sentences back to source chunks) is
brittle - paraphrased answers won't match chunk text, and the matching
logic becomes its own engineering problem. LLM-side citation constrains
the model to only cite chunk IDs we explicitly pass in the prompt, which
prevents hallucinated sources.
"""

import os
import json
from groq import Groq
from dotenv import load_dotenv

DEFAULT_MODEL = "llama-3.1-8b-instant"
MAX_CHUNKS_IN_PROMPT = 5

load_dotenv(os.path.join(os.path.dirname(__file__), "../../.env"))
GROQ_API_KEY = os.getenv("GROQ_API_KEY")


def _build_prompt(query: str, chunks: list[dict]) -> str:
    """
    Build the generation prompt from the query and retrieved chunks.

    NOTE: assumes each chunk has a chunk_id field - see KNOWN ISSUES in
    the module docstring above. retriever.py's real output does not
    currently provide this field.
    """
    chunk_blocks = []
    for i, chunk in enumerate(chunks):
        block = (
            f"[CHUNK {i}]\n"
            f"chunk_id: {chunk.get('chunk_id', i)}\n"
            f"source_file: {chunk.get('source_file', 'unknown')}\n"
            f"page_number: {chunk.get('page_number', 'unknown')}\n"
            f"text: {chunk.get('text', '')}"
        )
        chunk_blocks.append(block)

    chunks_text = "\n\n".join(chunk_blocks)

    return f"""You are a precise document QA assistant. Answer the user's query using ONLY the information in the provided chunks.

RULES:
1. Base your answer strictly on the chunks below. Do not use outside knowledge.
2. If the chunks do not contain enough information to answer, say so explicitly in the answer field.
3. For every claim in your answer, cite the chunk it came from using the chunk_id and source_file.
4. Only cite chunk_ids that appear in the list below. Never invent a chunk_id.
5. For each citation, copy the most relevant sentence or phrase from the chunk verbatim as the excerpt.
6. A single chunk can be cited multiple times if multiple claims come from it.
7. Respond ONLY with a valid JSON object. No preamble, no explanation, no markdown fences.

USER QUERY:
{query}

CHUNKS:
{chunks_text}

Respond with this exact JSON schema:
{{
    "answer": "<your synthesized answer>",
    "citations": [
        {{
            "chunk_id": <int>,
            "source_file": "<string>",
            "page_number": <int or null>,
            "excerpt": "<verbatim sentence or phrase from the chunk>"
        }}
    ]
}}"""


class GeneratorParseError(Exception):
    """Raised when the model's response cannot be parsed into the expected schema."""
    pass


def generate(query: str, chunks: list[dict], model_name: str = DEFAULT_MODEL,
             api_key: str = None) -> dict:
    """Generate an answer with citations using Groq. See module docstring KNOWN ISSUES."""
    if not query or not query.strip():
        raise ValueError("Query must be a non-empty string.")
    if not chunks:
        raise ValueError("At least one chunk must be provided for generation.")

    resolved_key = api_key or GROQ_API_KEY or os.environ.get("GROQ_API_KEY")
    if not resolved_key:
        raise EnvironmentError(
            "Groq API key not set. Set the GROQ_API_KEY environment variable "
            "in backend/.env."
        )

    capped_chunks = chunks[:MAX_CHUNKS_IN_PROMPT]
    prompt = _build_prompt(query, capped_chunks)

    try:
        client = Groq(api_key=resolved_key)
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": "You are a precise document QA assistant. Always respond with valid JSON only."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=1024,
        )
    except Exception as e:
        error_str = str(e).lower()
        if "api key" in error_str or "authentication" in error_str or "401" in error_str:
            raise EnvironmentError(f"Groq authentication failed: {e}") from e
        if "quota" in error_str or "rate" in error_str or "429" in error_str:
            raise RuntimeError(f"Groq rate limit exceeded: {e}") from e
        if "timeout" in error_str or "deadline" in error_str:
            raise TimeoutError(f"Groq request timed out: {e}") from e
        if "404" in error_str or "not found" in error_str:
            raise RuntimeError(f"Groq model not found: {e}") from e
        raise RuntimeError(f"Groq API call failed: {e}") from e

    raw_text = response.choices[0].message.content.strip()
    return _parse_response(raw_text, capped_chunks)


def _parse_response(raw_text: str, chunks: list[dict]) -> dict:
    if raw_text.startswith("```"):
        lines = raw_text.split("\n")
        raw_text = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise GeneratorParseError(f"Model response was not valid JSON.\nRaw response:\n{raw_text}\nError: {e}") from e

    if "answer" not in parsed:
        raise GeneratorParseError("Response missing required 'answer' field.")
    if "citations" not in parsed:
        raise GeneratorParseError("Response missing required 'citations' field.")
    if not isinstance(parsed["answer"], str):
        raise GeneratorParseError(f"'answer' must be a string, got {type(parsed['answer'])}.")
    if not isinstance(parsed["citations"], list):
        raise GeneratorParseError(f"'citations' must be a list, got {type(parsed['citations'])}.")

    valid_chunk_ids = {c.get("chunk_id") for c in chunks}
    cleaned_citations = []

    for i, citation in enumerate(parsed["citations"]):
        if not isinstance(citation, dict):
            raise GeneratorParseError(f"Citation {i} is not a dict: {citation}")
        for field in ("chunk_id", "source_file", "excerpt"):
            if field not in citation:
                raise GeneratorParseError(f"Citation {i} missing required field '{field}'.")
        if citation["chunk_id"] not in valid_chunk_ids:
            raise GeneratorParseError(
                f"Citation {i} references chunk_id={citation['chunk_id']} "
                f"which was not in the provided chunks. Valid chunk_ids: {sorted(valid_chunk_ids)}"
            )
        cleaned_citations.append({
            "chunk_id": citation["chunk_id"],
            "source_file": citation["source_file"],
            "page_number": citation.get("page_number"),
            "excerpt": citation["excerpt"],
        })

    return {"answer": parsed["answer"], "citations": cleaned_citations}
