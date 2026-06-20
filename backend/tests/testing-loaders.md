# Testing Notes — `loaders.py`

This documents what was tested in Phase 1, Chunk 1 (document loading) and why,
including a real bug that was caught and fixed before this code shipped.

> Note: this is a written record, not an automated test suite. For that, see
> `tests/test_loaders.py` (if/when it exists in this repo).

---

## What `loaders.py` does

Two functions, one shared contract:

| Function | Input | Output shape |
|---|---|---|
| `load_pdf(file_path)` | `.pdf` | `[{"page_number": int, "locator_type": "page", "text": str}, ...]` |
| `load_docx(file_path)` | `.docx` | `[{"page_number": int, "locator_type": "paragraph_index", "text": str}, ...]` |

Both return the same shape so downstream code (chunker, citation UI) can
handle either source type without caring which loader produced it.

---

## Why `locator_type` exists

PDFs have real, fixed pages. DOCX files don't — "pages" in Word depend on
fonts, margins, and the rendering engine, none of which are stored in the
file. So a DOCX loader can't return a true page number.

Instead, `load_docx` uses **paragraph position** as a stable proxy, and
tags every entry with `locator_type: "paragraph_index"` so nothing
downstream mistakes it for a real page. This lets the citation UI (Phase 4)
later render `"Page 4"` for PDFs and `"¶12"` for DOCX, correctly, without
needing to inspect the file extension itself — the data carries that
information with it.

---

## The bug that was caught before shipping

**Original (buggy) approach:** a counter that only incremented when a
paragraph had text:

```python
locator_index = 0
for para in doc.paragraphs:
    if para.text.strip():
        locator_index += 1
        # append with page_number = locator_index
```

**Problem:** this produces a *dense* count ("the Nth non-empty paragraph
I've seen"), not the paragraph's *true position* in the document. If a
blank paragraph was skipped, every paragraph after it would be mislabeled.

**Why this matters:** the entire point of `page_number` is citation
traceability — letting someone verify an answer by going back to the
source. If the number doesn't match the real position in the file, a user
checking a citation by counting paragraphs would land on the wrong one.
The bug wouldn't crash anything or throw an error — it would just be
silently, quietly wrong.

**Fix:** use the true index from `enumerate()`, same pattern `load_pdf`
already used for its `page_number`. Blank paragraphs are skipped from the
*output*, but not from the *count* — so gaps are preserved, not compacted.

```python
for i, para in enumerate(doc.paragraphs):
    if para.text.strip():
        # append with page_number = i + 1  (true position, gaps preserved)
```

---

## Verification performed

### Test 1 — DOCX true-position indexing
Built a test document with a known structure:

```
Position 1: "Paragraph A" — has content
Position 2: (blank)
Position 3: "Paragraph C" — has content
Position 4: (blank)
Position 5: (blank)
Position 6: "Paragraph F" — has content
```

**Expected:** `load_docx` returns entries at positions `[1, 3, 6]`
(matching the real document), not `[1, 2, 3]` (which the buggy dense
counter would have produced).

**Result:** ✅ Returned `[1, 3, 6]` — confirmed correct.

### Test 2 — PDF still works after the edit
Built a 2-page test PDF with known content on each page.

**Expected:** `load_pdf` returns 2 entries, each with `locator_type: "page"`.

**Result:** ✅ Returned both pages correctly, `locator_type` present and
correct on every entry.

### Test 3 — File integrity
Confirmed via checksum that the version tested was the exact version
shared, no ambiguity about "which copy" was verified.

---

## Takeaway

`page_number` always means **true position in the source document** —
gaps from skipped blank content are preserved, never compacted — for both
loaders. This is what makes citations trustworthy: a user (or an
interviewer asking "how do you know your citations are accurate?") can
verify any returned `page_number` by opening the source file and counting
to that exact spot.
