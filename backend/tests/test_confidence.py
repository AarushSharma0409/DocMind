"""
test_confidence.py — Phase 2, Chunk 4

Test suite for confidence.py.

Run with:
    python -m pytest tests/test_confidence.py -v

These are pure unit tests — no mocking needed because assess_confidence()
has no external dependencies. Every test is fully deterministic.
"""

import pytest
from app.retrieval.confidence import (
    assess_confidence,
    HIGH_SIMILARITY_THRESHOLD,
    LOW_SIMILARITY_THRESHOLD,
    MIN_CHUNKS_FOR_HIGH_CONFIDENCE
)

# ---------------------------------------------------------------------------
# Helpers — build minimal chunk dicts with only what assess_confidence needs
# ---------------------------------------------------------------------------

def make_chunks(*similarities: float) -> list[dict]:
    """Build a list of minimal chunk dicts with the given similarity scores."""
    return [{"similarity": s, "text": f"chunk {i}"} for i, s in enumerate(similarities)]


# ---------------------------------------------------------------------------
# 1. Output schema
# ---------------------------------------------------------------------------

class TestOutputSchema:
    def test_returns_dict(self):
        result = assess_confidence(make_chunks(0.7, 0.65))
        assert isinstance(result, dict)

    def test_contains_level_key(self):
        result = assess_confidence(make_chunks(0.7, 0.65))
        assert "level" in result

    def test_contains_reason_key(self):
        result = assess_confidence(make_chunks(0.7, 0.65))
        assert "reason" in result

    def test_level_is_valid_enum_value(self):
        valid_levels = {"high", "medium", "low"}
        for sim in [0.9, 0.5, 0.1, 0.0]:
            result = assess_confidence(make_chunks(sim))
            assert result["level"] in valid_levels, (
                f"sim={sim} produced invalid level: {result['level']!r}"
            )

    def test_reason_is_non_empty_string(self):
        result = assess_confidence(make_chunks(0.7, 0.65))
        assert isinstance(result["reason"], str)
        assert len(result["reason"]) > 0

    def test_no_extra_keys(self):
        result = assess_confidence(make_chunks(0.7, 0.65))
        assert set(result.keys()) == {"level", "reason"}


# ---------------------------------------------------------------------------
# 2. "high" confidence cases
# ---------------------------------------------------------------------------

class TestHighConfidence:
    def test_high_when_max_sim_above_threshold_and_enough_chunks(self):
        chunks = make_chunks(HIGH_SIMILARITY_THRESHOLD, HIGH_SIMILARITY_THRESHOLD)
        result = assess_confidence(chunks)
        assert result["level"] == "high"

    def test_high_with_several_strong_chunks(self):
        chunks = make_chunks(0.82, 0.74, 0.71, 0.68)
        result = assess_confidence(chunks)
        assert result["level"] == "high"

    def test_high_reason_mentions_chunk_count(self):
        chunks = make_chunks(0.80, 0.75)
        result = assess_confidence(chunks)
        assert "2" in result["reason"]

    def test_high_reason_mentions_best_similarity(self):
        chunks = make_chunks(0.80, 0.70)
        result = assess_confidence(chunks)
        # Best match should appear formatted in the reason string
        assert "0.80" in result["reason"]

    def test_high_requires_min_chunk_count(self):
        # One chunk, even with very high similarity, should NOT be "high"
        # because a single match is structurally weaker signal.
        chunks = make_chunks(0.99)
        result = assess_confidence(chunks)
        assert result["level"] != "high", (
            "A single chunk should not produce 'high' confidence regardless of "
            "its similarity score — one hit could be a partial match, not a "
            "genuine answer. MIN_CHUNKS_FOR_HIGH_CONFIDENCE exists to catch this."
        )


# ---------------------------------------------------------------------------
# 3. "medium" confidence cases
# ---------------------------------------------------------------------------

class TestMediumConfidence:
    def test_medium_when_sim_between_thresholds(self):
        sim = (HIGH_SIMILARITY_THRESHOLD + LOW_SIMILARITY_THRESHOLD) / 2
        chunks = make_chunks(sim, sim)
        result = assess_confidence(chunks)
        assert result["level"] == "medium"

    def test_medium_when_single_chunk_with_high_similarity(self):
        # High sim but only one chunk → medium, not high
        chunks = make_chunks(0.90)
        result = assess_confidence(chunks)
        assert result["level"] == "medium"

    def test_medium_when_sim_exactly_at_low_threshold(self):
        # At exactly LOW_SIMILARITY_THRESHOLD → medium (boundary is inclusive)
        chunks = make_chunks(LOW_SIMILARITY_THRESHOLD)
        result = assess_confidence(chunks)
        assert result["level"] == "medium"

    def test_medium_reason_mentions_chunk_count(self):
        chunks = make_chunks(0.50, 0.45)
        result = assess_confidence(chunks)
        assert "2" in result["reason"]

    def test_medium_reason_mentions_best_similarity(self):
        chunks = make_chunks(0.50, 0.45)
        result = assess_confidence(chunks)
        assert "0.50" in result["reason"]


# ---------------------------------------------------------------------------
# 4. "low" confidence cases
# ---------------------------------------------------------------------------

class TestLowConfidence:
    def test_low_when_all_sims_below_threshold(self):
        chunks = make_chunks(0.20, 0.15, 0.10)
        result = assess_confidence(chunks)
        assert result["level"] == "low"

    def test_low_when_max_sim_just_below_low_threshold(self):
        sim = LOW_SIMILARITY_THRESHOLD - 0.01
        chunks = make_chunks(sim)
        result = assess_confidence(chunks)
        assert result["level"] == "low"

    def test_low_when_all_sims_are_zero(self):
        chunks = make_chunks(0.0, 0.0, 0.0)
        result = assess_confidence(chunks)
        assert result["level"] == "low"

    def test_low_reason_mentions_best_similarity(self):
        chunks = make_chunks(0.20, 0.15)
        result = assess_confidence(chunks)
        assert "0.20" in result["reason"]

    def test_low_reason_mentions_threshold_or_grounding(self):
        chunks = make_chunks(0.10)
        result = assess_confidence(chunks)
        # The reason should mention something about reliability or grounding
        reason_lower = result["reason"].lower()
        assert any(word in reason_lower for word in ["threshold", "grounded", "reliable"])


# ---------------------------------------------------------------------------
# 5. Empty input (no chunks retrieved)
# ---------------------------------------------------------------------------

class TestEmptyInput:
    def test_empty_list_returns_low(self):
        result = assess_confidence([])
        assert result["level"] == "low"

    def test_empty_list_reason_mentions_no_chunks(self):
        result = assess_confidence([])
        reason_lower = result["reason"].lower()
        assert any(word in reason_lower for word in ["no", "not", "none"])

    def test_empty_list_does_not_crash(self):
        # Regression guard: max([]) raises ValueError — make sure we handle it
        try:
            assess_confidence([])
        except Exception as e:
            pytest.fail(f"assess_confidence([]) raised unexpectedly: {e}")


# ---------------------------------------------------------------------------
# 6. Threshold boundary conditions
# ---------------------------------------------------------------------------

class TestBoundaryConditions:
    def test_exactly_at_high_threshold_with_enough_chunks(self):
        chunks = make_chunks(HIGH_SIMILARITY_THRESHOLD, HIGH_SIMILARITY_THRESHOLD)
        result = assess_confidence(chunks)
        assert result["level"] == "high"

    def test_just_below_high_threshold_is_not_high(self):
        sim = HIGH_SIMILARITY_THRESHOLD - 0.001
        chunks = make_chunks(sim, sim)
        result = assess_confidence(chunks)
        assert result["level"] != "high"

    def test_exactly_at_low_threshold_is_not_low(self):
        chunks = make_chunks(LOW_SIMILARITY_THRESHOLD)
        result = assess_confidence(chunks)
        assert result["level"] != "low"

    def test_just_below_low_threshold_is_low(self):
        sim = LOW_SIMILARITY_THRESHOLD - 0.001
        chunks = make_chunks(sim)
        result = assess_confidence(chunks)
        assert result["level"] == "low"


# ---------------------------------------------------------------------------
# 7. Only the max similarity drives the level — not the average
# ---------------------------------------------------------------------------

class TestMaxSimilarityDrives:
    def test_one_strong_chunk_among_weak_ones_lifts_to_medium(self):
        # Average sim is low, but max is above LOW_SIMILARITY_THRESHOLD
        chunks = make_chunks(0.60, 0.05, 0.05, 0.05)
        result = assess_confidence(chunks)
        # 0.60 is above LOW_SIMILARITY_THRESHOLD (0.35) → should not be "low"
        assert result["level"] != "low", (
            "Max similarity (0.60) is above the low threshold, so confidence "
            "should not be 'low' even if the average is dragged down by weak chunks."
        )

    def test_all_chunks_identical_moderate_sim(self):
        chunks = make_chunks(0.50, 0.50, 0.50, 0.50, 0.50)
        result = assess_confidence(chunks)
        assert result["level"] == "medium"


# ---------------------------------------------------------------------------
# 8. Extra fields in chunk dicts are ignored
# ---------------------------------------------------------------------------

class TestRobustnessToExtraFields:
    def test_extra_fields_do_not_crash(self):
        chunks = [
            {
                "similarity": 0.75,
                "text": "some text",
                "source_file": "report.pdf",
                "page_number": 4,
                "locator_type": "page",
            },
            {
                "similarity": 0.70,
                "text": "other text",
                "source_file": "report.pdf",
                "page_number": 5,
                "locator_type": "page",
            },
        ]
        result = assess_confidence(chunks)
        assert result["level"] == "high"

    def test_real_retriever_output_shape_works(self):
        """Verify assess_confidence works with the exact shape retriever.py returns."""
        chunks = [
            {
                "text": "Revenue grew 12% year-over-year.",
                "source_file": "q3_report.pdf",
                "page_number": 4,
                "locator_type": "page",
                "similarity": 0.83,
            },
            {
                "text": "Operating expenses increased by 5%.",
                "source_file": "q3_report.pdf",
                "page_number": 7,
                "locator_type": "page",
                "similarity": 0.71,
            },
        ]
        result = assess_confidence(chunks)
        assert result["level"] == "high"
        assert "level" in result
        assert "reason" in result
