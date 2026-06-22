"""
confidence.py — Phase 2, Chunk 4

Confidence signaling layer for DocMind.

WHY THIS EXISTS AS A SEPARATE MODULE:
- retriever.py computes similarity scores but has no opinion about what they mean.
- generator.py produces answers but shouldn't be responsible for warning the user
  that its inputs were weak — mixing those two jobs makes the warning untestable
  in isolation and means a GenerationError silences the confidence signal entirely.
- This module is the judgment layer between them: reads retrieval output, returns
  a structured signal that the API layer (Phase 3) can surface independently of
  whether generation succeeded or failed.
"""

from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

# Thresholds are named constants so tuning them later means one change,
# not hunting through if-statements. These starting values are hypotheses —
# the right numbers come from observing real queries on real documents.
HIGH_SIMILARITY_THRESHOLD = 0.65
LOW_SIMILARITY_THRESHOLD = 0.35

# A single highly-similar chunk gets "medium" not "high" because one chunk
# is structurally fragile: it might be a genuine precise answer (fine) or
# the only partial hit in a weak retrieval (not fine). Two or more chunks
# independently corroborating a high score is a much stronger signal.
MIN_CHUNKS_FOR_HIGH_CONFIDENCE = 2


def assess_confidence(chunks: list[dict]) -> dict:
    """
    Given the output of retriever.retrieve(), return a confidence assessment.

    Args:
        chunks: List of chunk dicts from retriever.retrieve().
                Each dict must have a 'similarity' key (float, 0.0–1.0).

    Returns:
        {
            "level":  "high" | "medium" | "low",
            "reason": str   # human-readable explanation, suitable for the UI
        }

    This is a pure function — no API calls, no I/O, no side effects.
    It only reads the similarity scores already computed by retriever.py.
    """
    if not chunks:
        return {
            "level": "low",
            "reason": (
                "No relevant chunks were retrieved. The documents may not "
                "contain information about this query."
            ),
        }

    similarities = [c["similarity"] for c in chunks]
    max_sim = max(similarities)
    chunk_count = len(chunks)

    if (
        max_sim >= HIGH_SIMILARITY_THRESHOLD
        and chunk_count >= MIN_CHUNKS_FOR_HIGH_CONFIDENCE
    ):
        return {
            "level": "high",
            "reason": (
                f"Retrieved {chunk_count} chunks with strong similarity "
                f"(best match: {max_sim:.2f}). Answer is likely well-grounded."
            ),
        }
    elif max_sim >= LOW_SIMILARITY_THRESHOLD:
        return {
            "level": "medium",
            "reason": (
                f"Retrieved {chunk_count} chunk(s) with moderate similarity "
                f"(best match: {max_sim:.2f}). Answer may be partially grounded."
            ),
        }
    else:
        return {
            "level": "low",
            "reason": (
                f"Best similarity score was {max_sim:.2f}, which is below the "
                f"threshold for reliable retrieval. The answer may not be "
                f"grounded in the provided documents."
            ),
        }


if __name__ == "__main__":
    # Smoke test — runs four representative scenarios and prints results.
    # Uses the exact output shape that retriever.retrieve() produces so
    # you can verify this works end-to-end before wiring it into the API.

    scenarios = [
        {
            "label": "Strong retrieval (2 high-similarity chunks) → expect HIGH",
            "chunks": [
                {"text": "Revenue grew 12% year-over-year driven by APAC expansion.",
                 "source_file": "q3_report.pdf", "page_number": 4,
                 "locator_type": "page", "similarity": 0.82},
                {"text": "Operating expenses were held flat across all regions.",
                 "source_file": "q3_report.pdf", "page_number": 7,
                 "locator_type": "page", "similarity": 0.74},
            ],
        },
        {
            "label": "Single high-similarity chunk → expect MEDIUM (not HIGH)",
            "chunks": [
                {"text": "The defendant was acquitted on all charges.",
                 "source_file": "case_notes.pdf", "page_number": 2,
                 "locator_type": "page", "similarity": 0.91},
            ],
        },
        {
            "label": "Weak retrieval (low similarity scores) → expect LOW",
            "chunks": [
                {"text": "The weather in Q3 was mostly mild.",
                 "source_file": "q3_report.pdf", "page_number": 1,
                 "locator_type": "page", "similarity": 0.22},
                {"text": "Appendix A contains supplementary tables.",
                 "source_file": "q3_report.pdf", "page_number": 12,
                 "locator_type": "page", "similarity": 0.18},
            ],
        },
        {
            "label": "No chunks retrieved (empty collection or no match) → expect LOW",
            "chunks": [],
        },
    ]

    print("=" * 60)
    print("confidence.py — smoke test")
    print("=" * 60)

    for scenario in scenarios:
        result = assess_confidence(scenario["chunks"])
        print(f"\nScenario : {scenario['label']}")
        print(f"Level    : {result['level'].upper()}")
        print(f"Reason   : {result['reason']}")

    print("\n" + "=" * 60)
    print("Smoke test complete.")