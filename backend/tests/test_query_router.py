"""
test_query_router.py - Phase 2, Chunk 2 tests

WHY THIS EXISTS:
query_router.py decides which retrieval strategy a query gets - a wrong
or silently-failing routing decision means every later piece (retrieval,
generation, citation) operates on the wrong assumption. Two real bugs
were found and fixed during development (missing API key handling, and
a Gemini schema validation error from an unsupported nullable type) -
this suite locks in the fixes as permanent regression tests.

WHY NO REAL API CALLS IN TESTS: route_query() makes a real, paid Gemini
API call. Tests should not depend on network access, a valid API key, or
real model output (which isn't perfectly deterministic) to pass reliably
and for free. Instead, these tests verify the LOGIC around the API call:
prompt construction, response parsing, sentinel conversion, hallucination
defense, and fallback behavior - by calling the internal parsing/prompt
functions directly, or by mocking the client at the route_query level.
"""

import pytest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.retrieval.query_router import (
    route_query,
    _parse_routing_response,
    _build_classification_prompt,
    VALID_ROUTES,
    DEFAULT_FALLBACK_ROUTE,
    NO_TARGET_DOCUMENT_SENTINEL,
    ROUTE_RESPONSE_SCHEMA,
)


# ---------------------------------------------------------------------------
# route_query - empty/whitespace short-circuiting (no API call needed)
# ---------------------------------------------------------------------------

def test_route_query_empty_string_returns_fallback_without_api_call():
    result = route_query("", available_documents=["a.pdf"])
    assert result == {"route": DEFAULT_FALLBACK_ROUTE, "target_document": None}


def test_route_query_whitespace_only_returns_fallback_without_api_call():
    result = route_query("   \n  ", available_documents=["a.pdf"])
    assert result == {"route": DEFAULT_FALLBACK_ROUTE, "target_document": None}


def test_route_query_handles_none_available_documents():
    """available_documents=None (not passed) should not crash - treated as empty list."""
    result = route_query("")  # empty query short-circuits before documents matter
    assert result == {"route": DEFAULT_FALLBACK_ROUTE, "target_document": None}


# ---------------------------------------------------------------------------
# route_query - API failure handling
# ---------------------------------------------------------------------------

def test_route_query_falls_back_on_api_exception(monkeypatch):
    """
    REGRESSION TEST for the missing-API-key bug found during development.

    Any exception during the API call (missing key, network failure,
    rate limit, schema validation error) must result in the safe
    fallback, not an unhandled crash propagating to the caller.
    """
    import app.retrieval.query_router as router_module

    def raise_error():
        raise ValueError("No API key was provided.")

    monkeypatch.setattr(router_module, "get_client", raise_error)

    result = route_query("a real question", available_documents=["a.pdf"])
    assert result == {"route": DEFAULT_FALLBACK_ROUTE, "target_document": None}


def test_route_query_falls_back_on_any_exception_type(monkeypatch):
    """Fallback behavior shouldn't be specific to one exception type - any failure degrades safely."""
    import app.retrieval.query_router as router_module

    class FakeClientThatFails:
        class models:
            @staticmethod
            def generate_content(*args, **kwargs):
                raise RuntimeError("simulated network failure")

    monkeypatch.setattr(router_module, "get_client", lambda: FakeClientThatFails())

    result = route_query("a real question", available_documents=[])
    assert result == {"route": DEFAULT_FALLBACK_ROUTE, "target_document": None}


# ---------------------------------------------------------------------------
# route_query - successful classification (mocked client)
# ---------------------------------------------------------------------------

def _make_fake_client(response_text: str):
    """Build a fake Gemini client whose generate_content returns a fixed response."""
    class FakeResponse:
        text = response_text

    class FakeModels:
        @staticmethod
        def generate_content(*args, **kwargs):
            return FakeResponse()

    class FakeClient:
        models = FakeModels()

    return FakeClient()


def test_route_query_returns_parsed_result_on_success(monkeypatch):
    import app.retrieval.query_router as router_module

    fake_client = _make_fake_client(
        '{"route": "full_document", "target_document": "report.pdf"}'
    )
    monkeypatch.setattr(router_module, "get_client", lambda: fake_client)

    result = route_query("summarize report.pdf", available_documents=["report.pdf"])
    assert result == {"route": "full_document", "target_document": "report.pdf"}


def test_route_query_converts_sentinel_to_none_on_success(monkeypatch):
    import app.retrieval.query_router as router_module

    fake_client = _make_fake_client(
        f'{{"route": "no_retrieval", "target_document": "{NO_TARGET_DOCUMENT_SENTINEL}"}}'
    )
    monkeypatch.setattr(router_module, "get_client", lambda: fake_client)

    result = route_query("hello", available_documents=["report.pdf"])
    assert result == {"route": "no_retrieval", "target_document": None}


# ---------------------------------------------------------------------------
# _parse_routing_response - the core parsing/validation logic
# ---------------------------------------------------------------------------

def test_parse_valid_retrieve_response():
    result = _parse_routing_response(
        f'{{"route": "retrieve", "target_document": "{NO_TARGET_DOCUMENT_SENTINEL}"}}', []
    )
    assert result == {"route": "retrieve", "target_document": None}


def test_parse_valid_full_document_response_with_known_document():
    result = _parse_routing_response(
        '{"route": "full_document", "target_document": "report.pdf"}', ["report.pdf"]
    )
    assert result == {"route": "full_document", "target_document": "report.pdf"}


def test_parse_rejects_hallucinated_filename_not_in_available_documents():
    """
    A filename the model invented (not in the actual available_documents
    list) must be treated as None, not trusted - downstream code would
    otherwise try to look up a document that was never uploaded.
    """
    result = _parse_routing_response(
        '{"route": "full_document", "target_document": "totally_made_up.pdf"}',
        ["report.pdf", "notes.docx"],
    )
    assert result == {"route": "full_document", "target_document": None}


def test_parse_malformed_json_falls_back():
    result = _parse_routing_response("this is not valid json at all", [])
    assert result == {"route": DEFAULT_FALLBACK_ROUTE, "target_document": None}


def test_parse_invalid_route_value_falls_back():
    """A route value outside VALID_ROUTES (even if JSON is otherwise valid) must fall back."""
    result = _parse_routing_response(
        '{"route": "do_something_unexpected", "target_document": "none"}', []
    )
    assert result == {"route": DEFAULT_FALLBACK_ROUTE, "target_document": None}


def test_parse_missing_route_key_falls_back():
    result = _parse_routing_response('{"target_document": "none"}', [])
    assert result == {"route": DEFAULT_FALLBACK_ROUTE, "target_document": None}


def test_parse_empty_json_object_falls_back():
    result = _parse_routing_response("{}", [])
    assert result == {"route": DEFAULT_FALLBACK_ROUTE, "target_document": None}


@pytest.mark.parametrize("route", VALID_ROUTES)
def test_parse_accepts_every_valid_route(route):
    """Each of the three valid routes should parse correctly, not just one or two."""
    result = _parse_routing_response(
        f'{{"route": "{route}", "target_document": "{NO_TARGET_DOCUMENT_SENTINEL}"}}', []
    )
    assert result["route"] == route


# ---------------------------------------------------------------------------
# Schema shape - the Gemini nullable-type regression
# ---------------------------------------------------------------------------

def test_response_schema_does_not_use_nullable_type_arrays():
    """
    REGRESSION TEST for the Gemini schema validation bug found during
    development.

    Gemini's response_schema does NOT support JSON-Schema-style type
    unions like ["string", "null"] - every API call failed with a
    pydantic ValidationError until this was fixed. This test locks in
    that target_document's "type" field is a single string value, not
    a list, so this specific regression can't silently come back.
    """
    target_doc_type = ROUTE_RESPONSE_SCHEMA["properties"]["target_document"]["type"]
    assert isinstance(target_doc_type, str), (
        f"target_document's schema type must be a single string (e.g. "
        f'"string"), not a list/union - Gemini\'s response_schema does '
        f"not support nullable type arrays. Got: {target_doc_type!r}"
    )


def test_response_schema_route_field_uses_valid_enum():
    route_schema = ROUTE_RESPONSE_SCHEMA["properties"]["route"]
    assert set(route_schema["enum"]) == set(VALID_ROUTES)


# ---------------------------------------------------------------------------
# _build_classification_prompt - prompt construction
# ---------------------------------------------------------------------------

def test_build_prompt_includes_the_query():
    prompt = _build_classification_prompt("What did the report say?", [])
    assert "What did the report say?" in prompt


def test_build_prompt_includes_available_documents():
    prompt = _build_classification_prompt("a query", ["report.pdf", "notes.docx"])
    assert "report.pdf" in prompt
    assert "notes.docx" in prompt


def test_build_prompt_handles_no_documents_gracefully():
    """Should not crash or produce a malformed prompt when no documents exist yet."""
    prompt = _build_classification_prompt("hello", [])
    assert "no documents uploaded yet" in prompt.lower()


def test_build_prompt_instructs_sentinel_usage():
    """The prompt must explicitly tell the model to use the sentinel, not JSON null."""
    prompt = _build_classification_prompt("a query", [])
    assert NO_TARGET_DOCUMENT_SENTINEL in prompt
