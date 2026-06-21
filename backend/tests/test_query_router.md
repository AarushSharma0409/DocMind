# Test Plan: `query_router.py`

**Module:** `query_router.py`  
**Classifier:** Gemini (structured JSON output)  
**Purpose:** Route incoming user queries to the correct retrieval strategy — vector search, web search, or hybrid.

---

## Scope

The router does one job: given a query string, return a routing decision. Tests cover classification accuracy, output schema validity, edge cases, and failure handling. They do **not** test downstream retrieval — that's Chunk 1's responsibility.

---

## Route Categories

| Route | Trigger condition |
|---|---|
| `vector` | Query answerable from ingested documents |
| `web` | Query requires current/external knowledge |
| `hybrid` | Query needs both document context + external info |

---

## Test Cases

### 1. Schema & Output Validation

| ID | Test | Expected |
|---|---|---|
| R-01 | Router returns a dict/object | `isinstance(result, dict) == True` |
| R-02 | Output contains `route` key | `"route" in result` |
| R-03 | `route` value is one of the valid enum values | `result["route"] in {"vector", "web", "hybrid"}` |
| R-04 | Output contains `confidence` or `reasoning` field (if implemented) | Field present and non-empty |
| R-05 | No extra keys bleed through from raw Gemini response | Result keys == expected schema keys only |

---

### 2. Classification Accuracy — `vector` Route

| ID | Query | Expected Route |
|---|---|---|
| R-10 | `"What does the document say about data preprocessing?"` | `vector` |
| R-11 | `"Summarize the uploaded PDF"` | `vector` |
| R-12 | `"What are the key findings in the report?"` | `vector` |
| R-13 | `"List all section headings in the document"` | `vector` |

---

### 3. Classification Accuracy — `web` Route

| ID | Query | Expected Route |
|---|---|---|
| R-20 | `"What is the current price of Bitcoin?"` | `web` |
| R-21 | `"Latest news on OpenAI"` | `web` |
| R-22 | `"Who won the 2024 US election?"` | `web` |
| R-23 | `"What is today's weather in Delhi?"` | `web` |

---

### 4. Classification Accuracy — `hybrid` Route

| ID | Query | Expected Route |
|---|---|---|
| R-30 | `"Compare what the document says about X with recent research"` | `hybrid` |
| R-31 | `"Is the methodology in this paper still considered best practice?"` | `hybrid` |
| R-32 | `"What does the report say, and are there any recent updates on this topic?"` | `hybrid` |

---

### 5. Edge Cases

| ID | Test | Expected |
|---|---|---|
| R-40 | Empty string query `""` | Raises `ValueError` or returns `{"route": "vector"}` with low confidence — must not crash silently |
| R-41 | Whitespace-only query `"   "` | Same as R-40 |
| R-42 | Single word query `"summarize"` | Should not error; likely routes to `vector` |
| R-43 | Very long query (>500 tokens) | Routes correctly without truncation errors |
| R-44 | Query in non-English language | Routes without crash; route accuracy not guaranteed but no exception |
| R-45 | Query with special characters `"What's the doc about?!@#"` | No crash, valid route returned |

---

### 6. Failure & Fallback Handling

| ID | Test | Expected |
|---|---|---|
| R-50 | Gemini API key missing / invalid | Raises clear `AuthenticationError` or custom exception — not a silent wrong route |
| R-51 | Gemini API rate limit hit (mock) | Raises `RateLimitError` or retries with backoff |
| R-52 | Gemini returns malformed JSON | Router catches parse error and either retries or raises `RouterParseError` |
| R-53 | Gemini returns valid JSON but with unknown `route` value | Router raises `ValueError` — unknown routes must not pass through |
| R-54 | Network timeout (mock) | Raises `TimeoutError`, does not hang indefinitely |

---

### 7. Performance (Optional / Future)

| ID | Test | Expected |
|---|---|---|
| R-60 | Single query latency | < 3s under normal conditions |
| R-61 | 10 sequential queries | No memory leak, consistent latency |

---

## Test Infrastructure Notes

- Use `pytest` with `unittest.mock` or `pytest-mock` to mock Gemini SDK calls for failure tests (R-50 to R-54).
- Accuracy tests (R-10 to R-32) should call the real Gemini API — do not mock these, as the point is validating LLM classification behavior.
- Mark live API tests with `@pytest.mark.integration` to allow selective skipping in CI.
- Store expected routes as constants, not inline strings, to avoid typo drift.

---

## Acceptance Criteria

- All schema tests (R-01 to R-05) pass 100%.
- Classification accuracy tests (R-10 to R-32): ≥ 90% correct routes (at least 10/11 per category).
- All edge case tests (R-40 to R-45) pass without unhandled exceptions.
- All failure tests (R-50 to R-54) raise the correct exception type — no silent failures.
