# Testing Notes — `loaders.py`

This documents what was tested in Phase 1, Chunk 1 (document loading) and why,
including a real bug that was caught and fixed before this code shipped, and
the OCR fallback added in Phase 3.

> This is a written record for context and interview prep. The actual
> automated tests live in `tests/test_loaders.py` — run them with
> `python -m pytest tests/test_loaders.py -v`.

---

## What `loaders.py` does

Three functions, one shared output contract:

| Function | Input | Output shape |
|---|---|---|
| `load_pdf(file_path)` | `.pdf` | `[{"page_number": int, "locator_type": "page", "text": str}, ...]` |
| `load_docx(file_path)` | `.docx` | `[{"page_number": int, "locator_type": "paragraph_index", "text": str}, ...]` |
| `_ocr_pdf_page(file_path, page_number)` | `.pdf` + page index | `str` (OCR text for one page, internal helper) |

All loaders return the same shape so downstream code (chunker, citation UI)
can handle any source type without caring which loader produced it.

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

## OCR fallback (added Phase 3)

The original `load_pdf()` skipped pages with no extractable text — correct
for digitally-created PDFs, but it meant scanned documents returned zero
pages and were rejected at upload.

**The fix:** a per-page OCR fallback using Tesseract + Poppler. If `pypdf`
returns no text for a page, `_ocr_pdf_page()` converts that page to a 300
DPI image and runs OCR on it. The output shape is identical to the
text-extraction path — callers don't know or care which path ran.

**Why per-page, not whole-document:** converting an entire PDF to images
upfront holds all pages in memory simultaneously. Per-page means only pages
that need OCR pay the cost. Most documents are fully text-based (no OCR
calls at all) or fully scanned (all pages need it, but one at a time).

**Why hardcoded paths:** Tesseract and Poppler PATH changes don't propagate
reliably on Windows across shells and virtual environments. Hardcoding the
executable paths (`pytesseract.pytesseract.tesseract_cmd` and
`poppler_path=`) is the standard reliable pattern for Windows Python
projects using Tesseract.

**Graceful degradation:** if OCR fails for any reason, the exception is
caught and the page is skipped — same behavior as a text-based empty page.
The loader still works for text-based PDFs even if Tesseract/Poppler aren't
installed (`OCR_AVAILABLE = False`).

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

### Test 2 — PDF text extraction still works
Built a 2-page test PDF with known content on each page.

**Expected:** `load_pdf` returns 2 entries, each with `locator_type: "page"`.

**Result:** ✅ Returned both pages correctly.

### Test 3 — Scanned PDF via OCR (Phase 3)
Uploaded `final_unit_3.pdf` — a 15-page fully scanned document with no
embedded text layer. Previously rejected with "no extractable text."

**Expected:** OCR extracts text from each page, 15 chunks stored.

**Result:** ✅ `200 OK`, `chunks_stored: 15`. All pages processed via OCR.

### Test 4 — Text-based PDF unaffected by OCR addition
Uploaded `Transformers_Overview_3_Pages.pdf` — digitally created.

**Expected:** Fast path (no OCR), 3 pages extracted.

**Result:** ✅ 3 pages extracted correctly. OCR code never called.

### Test 5 — File integrity
Confirmed via checksum that the version tested was the exact version
shared, no ambiguity about "which copy" was verified.

---

## Takeaway

`page_number` always means **true position in the source document** —
gaps from skipped blank content are preserved, never compacted — for both
loaders. OCR pages use the same page numbering convention as text-based
pages, so citations from scanned documents are just as traceable as those
from digital ones.

The OCR addition is worth calling out in an interview: scanned documents
are extremely common in enterprise contexts, and a RAG system that silently
rejects them is much less useful than one that handles them transparently.