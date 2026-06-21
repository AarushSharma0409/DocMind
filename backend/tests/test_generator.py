"""
test_generator.py - Phase 2, Chunk 3 tests

WHY THIS EXISTS:
generator.py is what turns retrieved chunks into the actual answer and
citations a user sees - if citation hydration is wrong, or a malformed
model response isn't caught, the user either sees a broken answer or
trusts an uncited/miscited claim. This suite locks in correct behavior
for the parts of the pipeline that don't require a live API call, plus
optional integration tests that do.

WHY MOSTLY MOCKED: generate() makes a real, paid Gemini API call. Tests
should not depend on network access, a valid API key, or non-deterministic
real model output to pass reliably and for free. The unit tests below
verify the LOGIC: prompt construction, response parsing, citation
hydration from chunk_index back to full source metadata, and the
out-of-range-index defense - by mocking the Gemini client directly.

WHY A SEPARATE INTEGRATION CLASS: a handful of tests genuinely exercise
the real API, marked with @pytest.mark.integration so they can be
skipped by default (e.g. `pytest -m "not integration"`) and only run
deliberately when checking real model behavior, since they cost money
and depend on network access. This pattern was adapted from an earlier
Groq-based draft of this module, which used the same split well.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.retrieval.generator import (
    generate,
    _build_generation_prompt,
    _parse_generation_response,
    GenerationError,
    MAX_CHUNKS_IN_PROMPT,
)


# ---------------------------------------------------------------------------
# Fixtures - chunks shaped EXACTLY like retriever.py's real output
# (no chunk_id field - that was a bug in an earlier draft of this module)
# ---------------------------------------------------------------------------

@pytest.fixture
def single_chunk():
    return [
        {
            "text": "The company reported a net revenue of $4.2 billion in fiscal year 2023, "
                    "representing a 12% year-over-year growth driven by expansion in APAC markets.",
            "source_file": "report.pdf",
            "page_number": 2,
            "locator_type": "page",
            "similarity": 0.91,
        }
    ]


@pytest.fixture
def multi_chunk():
    return [
        {
            "text": "The company reported a net revenue of $4.2 billion in fiscal year 2023, "
                    "representing a 12% year-over-year growth driven by expansion in APAC markets.",
            "source_file": "report.pdf",
            "page_number": 2,
            "locator_type": "page",
            "similarity": 0.91,
        },
        {
            "text": "Operating expenses increased by 8% primarily due to headcount growth "
                    "in the engineering and sales divisions.",
            "source_file": "report.pdf",
            "page_number": 3,
            "locator_type": "page",
            "similarity": 0.84,
        },
        {
            "text": "The study used a mixed-methods approach combining quantitative surveys "
                    "with qualitative interviews across five regions.",
            "source_file": "methodology.docx",
            "page_number": 5,
            "locator_type": "paragraph_index",
            "similarity": 0.76,
        },
    ]


def make_fake_gemini_response(payload: dict):
    """Build a fake Gemini response object whose .text returns the given payload as JSON."""
    fake_response = MagicMock()
    fake_response.text = json.dumps(payload)
    return fake_response


def make_fake_client(response_payload: dict):
    """Build a fake genai.Client whose models.generate_content returns a fixed response."""
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = make_fake_gemini_response(response_payload)
    return fake_client


# ---------------------------------------------------------------------------
# _build_generation_prompt - pure unit, no API
# ---------------------------------------------------------------------------

class TestBuildPrompt:
    def test_contains_query(self, single_chunk):
        prompt = _build_generation_prompt("What is the revenue?", single_chunk)
        assert "What is the revenue?" in prompt

    def test_contains_chunk_text(self, single_chunk):
        prompt = _build_generation_prompt("query", single_chunk)
        assert single_chunk[0]["text"] in prompt

    def test_contains_chunk_metadata(self, single_chunk):
        prompt = _build_generation_prompt("query", single_chunk)
        assert "report.pdf" in prompt
        assert "page_number: 2" in prompt

    def test_uses_chunk_index_not_chunk_id(self, single_chunk):
        """
        Confirms this module's prompt is built around chunk_index (the
        chunk's position in the list), NOT a chunk_id field - which
        retriever.py's real output does not provide. An earlier draft
        assumed chunk_id; this is the regression check that it's gone.
        """
        prompt = _build_generation_prompt("query", single_chunk)
        assert "[CHUNK 0]" in prompt
        assert "chunk_id" not in prompt.lower()

    def test_multiple_chunks_all_present_with_correct_indices(self, multi_chunk):
        prompt = _build_generation_prompt("query", multi_chunk)
        assert "[CHUNK 0]" in prompt
        assert "[CHUNK 1]" in prompt
        assert "[CHUNK 2]" in prompt
        for chunk in multi_chunk:
            assert chunk["text"] in prompt

    def test_missing_source_file_falls_back_to_unknown(self):
        chunks = [{"page_number": 1, "text": "some text", "locator_type": "page"}]
        prompt = _build_generation_prompt("query", chunks)
        assert "unknown" in prompt

    def test_states_valid_index_range(self, multi_chunk):
        """The prompt should explicitly tell the model the valid chunk_index range."""
        prompt = _build_generation_prompt("query", multi_chunk)
        assert "0 to 2" in prompt


# ---------------------------------------------------------------------------
# _parse_generation_response - pure unit, no API
# ---------------------------------------------------------------------------

class TestParseGenerationResponse:

    def test_valid_response_returns_correct_shape(self, single_chunk):
        raw = json.dumps({
            "answer": "Revenue grew 12%.",
            "citations": [{"chunk_index": 0, "excerpt": "representing a 12% year-over-year growth"}],
        })
        result = _parse_generation_response(raw, single_chunk)
        assert "answer" in result
        assert "citations" in result
        assert isinstance(result["answer"], str)
        assert isinstance(result["citations"], list)

    def test_citation_hydrated_with_full_source_metadata(self, single_chunk):
        """
        THE CORE CITATION TEST: a chunk_index-based citation must be
        expanded into the full source record (source_file, page_number,
        locator_type) using the actual chunk's metadata - this is the
        entire point of this module's citation design.
        """
        raw = json.dumps({
            "answer": "Revenue grew 12%.",
            "citations": [{"chunk_index": 0, "excerpt": "12% year-over-year growth"}],
        })
        result = _parse_generation_response(raw, single_chunk)
        citation = result["citations"][0]
        assert citation["source_file"] == "report.pdf"
        assert citation["page_number"] == 2
        assert citation["locator_type"] == "page"
        assert citation["excerpt"] == "12% year-over-year growth"

    def test_citation_preserves_locator_type_for_docx_source(self, multi_chunk):
        """Citation hydration must correctly carry paragraph_index locator_type too, not just 'page'."""
        raw = json.dumps({
            "answer": "Methodology used surveys.",
            "citations": [{"chunk_index": 2, "excerpt": "mixed-methods approach"}],
        })
        result = _parse_generation_response(raw, multi_chunk)
        assert result["citations"][0]["source_file"] == "methodology.docx"
        assert result["citations"][0]["locator_type"] == "paragraph_index"

    def test_out_of_range_chunk_index_is_dropped_not_crashed(self, single_chunk):
        """
        THE CORE HALLUCINATION-DEFENSE TEST.

        An out-of-bounds chunk_index (the model citing a chunk that
        wasn't actually provided) must be silently dropped, not crash
        the whole response - same defensive principle as
        query_router.py's hallucinated-filename guard.
        """
        raw = json.dumps({
            "answer": "Some answer.",
            "citations": [
                {"chunk_index": 0, "excerpt": "valid"},
                {"chunk_index": 99, "excerpt": "out of range, should be dropped"},
            ],
        })
        result = _parse_generation_response(raw, single_chunk)
        assert len(result["citations"]) == 1
        assert result["citations"][0]["excerpt"] == "valid"

    def test_negative_chunk_index_is_dropped(self, single_chunk):
        raw = json.dumps({"answer": "x", "citations": [{"chunk_index": -1, "excerpt": "bad"}]})
        result = _parse_generation_response(raw, single_chunk)
        assert result["citations"] == []

    def test_non_integer_chunk_index_is_dropped(self, single_chunk):
        raw = json.dumps({"answer": "x", "citations": [{"chunk_index": "not a number", "excerpt": "bad"}]})
        result = _parse_generation_response(raw, single_chunk)
        assert result["citations"] == []

    def test_empty_citations_list_is_valid(self, single_chunk):
        """Model may legitimately find nothing worth citing - not an error."""
        raw = json.dumps({"answer": "I don't know based on these documents.", "citations": []})
        result = _parse_generation_response(raw, single_chunk)
        assert result["citations"] == []

    def test_multiple_citations_from_same_chunk(self, single_chunk):
        raw = json.dumps({
            "answer": "Revenue grew due to APAC expansion.",
            "citations": [
                {"chunk_index": 0, "excerpt": "12% year-over-year growth"},
                {"chunk_index": 0, "excerpt": "expansion in APAC markets"},
            ],
        })
        result = _parse_generation_response(raw, single_chunk)
        assert len(result["citations"]) == 2

    def test_malformed_json_raises_generation_error(self, single_chunk):
        with pytest.raises(GenerationError, match="not valid JSON"):
            _parse_generation_response("this is not json", single_chunk)

    def test_missing_answer_field_raises(self, single_chunk):
        raw = json.dumps({"citations": []})
        with pytest.raises(GenerationError, match="answer"):
            _parse_generation_response(raw, single_chunk)

    def test_missing_citations_field_raises(self, single_chunk):
        raw = json.dumps({"answer": "some answer"})
        with pytest.raises(GenerationError, match="citations"):
            _parse_generation_response(raw, single_chunk)

    def test_answer_wrong_type_raises(self, single_chunk):
        raw = json.dumps({"answer": 123, "citations": []})
        with pytest.raises(GenerationError, match="answer"):
            _parse_generation_response(raw, single_chunk)

    def test_malformed_citation_entry_is_skipped_not_crashed(self, single_chunk):
        """A non-dict citation entry should be skipped, not crash the whole parse."""
        raw = json.dumps({
            "answer": "ok",
            "citations": ["not a dict", {"chunk_index": 0, "excerpt": "valid one"}],
        })
        result = _parse_generation_response(raw, single_chunk)
        assert len(result["citations"]) == 1


# ---------------------------------------------------------------------------
# generate() - mocked (no real API call)
# ---------------------------------------------------------------------------

class TestGenerateMocked:

    def test_returns_correct_schema(self, monkeypatch, single_chunk):
        import app.retrieval.generator as gen_module
        fake_client = make_fake_client({
            "answer": "Revenue grew 12%.",
            "citations": [{"chunk_index": 0, "excerpt": "12% year-over-year growth"}],
        })
        monkeypatch.setattr(gen_module, "get_client", lambda: fake_client)

        result = generate("What is the revenue?", single_chunk)
        assert "answer" in result
        assert "citations" in result

    def test_caps_chunks_at_max(self, monkeypatch):
        """Passing more than MAX_CHUNKS_IN_PROMPT chunks should only send the cap's worth to the model."""
        import app.retrieval.generator as gen_module
        fake_client = make_fake_client({"answer": "x", "citations": []})
        monkeypatch.setattr(gen_module, "get_client", lambda: fake_client)

        many_chunks = [
            {"text": f"text {i}", "source_file": "f.pdf", "page_number": i, "locator_type": "page"}
            for i in range(10)
        ]
        generate("query", many_chunks)

        call_kwargs = fake_client.models.generate_content.call_args.kwargs
        prompt_sent = call_kwargs["contents"]
        assert f"[CHUNK {MAX_CHUNKS_IN_PROMPT - 1}]" in prompt_sent
        assert f"[CHUNK {MAX_CHUNKS_IN_PROMPT}]" not in prompt_sent

    def test_empty_query_raises_value_error(self, single_chunk):
        with pytest.raises(ValueError, match="non-empty"):
            generate("", single_chunk)

    def test_whitespace_query_raises_value_error(self, single_chunk):
        with pytest.raises(ValueError, match="non-empty"):
            generate("   ", single_chunk)

    def test_empty_chunks_raises_value_error(self):
        with pytest.raises(ValueError, match="At least one chunk"):
            generate("valid query", [])

    def test_api_failure_raises_generation_error(self, monkeypatch, single_chunk):
        """
        Any underlying API failure (network, auth, rate limit) should
        surface as the single GenerationError type, not propagate the
        raw underlying exception - see module docstring for why a single
        exception type is used here (unlike the multi-exception Groq
        backup draft).
        """
        import app.retrieval.generator as gen_module

        def raise_error():
            raise RuntimeError("simulated network failure")

        monkeypatch.setattr(gen_module, "get_client", raise_error)

        with pytest.raises(GenerationError, match="Answer generation failed"):
            generate("a real question", single_chunk)

    def test_malformed_model_response_raises_generation_error(self, monkeypatch, single_chunk):
        import app.retrieval.generator as gen_module
        fake_client = MagicMock()
        fake_response = MagicMock()
        fake_response.text = "not valid json"
        fake_client.models.generate_content.return_value = fake_response
        monkeypatch.setattr(gen_module, "get_client", lambda: fake_client)

        with pytest.raises(GenerationError):
            generate("query", single_chunk)


# ---------------------------------------------------------------------------
# generate() - integration (real Gemini API, costs money, needs network)
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestGenerateIntegration:
    """
    Run explicitly with: pytest tests/test_generator.py -m integration -v
    Skipped by default in a normal `pytest tests/` run via the project's
    pytest configuration (or by running `pytest -m "not integration"`).
    """

    def test_smoke_single_chunk(self, single_chunk):
        result = generate("What was the revenue growth and what drove it?", single_chunk)
        assert isinstance(result["answer"], str)
        assert len(result["answer"]) > 10
        assert isinstance(result["citations"], list)

    def test_answer_is_grounded_in_chunk_content(self, single_chunk):
        result = generate("What drove revenue growth?", single_chunk)
        answer_lower = result["answer"].lower()
        assert "apac" in answer_lower or "12%" in answer_lower or "expansion" in answer_lower

    def test_citations_have_non_empty_excerpts(self, multi_chunk):
        result = generate("What were the operating expenses and methodology?", multi_chunk)
        for citation in result["citations"]:
            assert citation["excerpt"] and len(citation["excerpt"]) > 5

    def test_unanswerable_query_does_not_crash(self, single_chunk):
        """If chunks don't contain the answer, the model should say so, not error out."""
        result = generate("What is the CEO's name?", single_chunk)
        assert isinstance(result["answer"], str)
        assert len(result["answer"]) > 0