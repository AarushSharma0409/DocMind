# Testing Notes — OCR Fallback in `loaders.py`

This documents what was added and verified in Phase 3 (OCR support) — an
extension to `load_pdf()` that handles scanned/image-based PDFs that contain
no extractable text.

> The updated loader lives at `backend/app/ingestion/loaders.py`. No new
> test file was created for OCR specifically — the behavioral contract is
> verified below and the existing `test_loaders.py` suite still covers the
> text-extraction path.

---

## What changed and why

The original `load_pdf()` skipped pages with no extractable text. For
digitally-created PDFs this is correct — empty pages should be skipped.
But for scanned PDFs (images of pages with no embedded text layer), this
meant the entire document would return zero pages, and the upload endpoint
would correctly reject it with a 422 error.

The fix adds a per-page OCR fallback: if `pypdf` returns no text for a
page, `pdf2image` converts that page to a 300 DPI image and `pytesseract`
runs OCR on it. Pages with real embedded text still take the fast path —
no OCR overhead for documents that don't need it.

---

## Why per-page OCR, not whole-document

Converting an entire PDF to images upfront is memory-intensive. A 50-page
document at 300 DPI produces ~50 large PIL images held in memory
simultaneously. Per-page conversion means only pages that actually need OCR
pay the cost — in practice, most documents are either fully text-based (zero
OCR calls) or fully scanned (all pages need it, but one at a time).

---

## Why hardcoded paths, not PATH reliance

Both Tesseract and Poppler require system-level installation. On Windows,
PATH changes don't always propagate reliably across shells, especially in
virtual environments. Rather than fighting PATH, the loader sets paths
explicitly:

```python
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
```

```python
poppler_path=r"C:\Program Files\poppler-26.02.0\Library\bin"
```

This is the standard production pattern for Windows Python projects using
Tesseract — more reliable than PATH, and explicitly documented so any
developer setting up the project knows exactly what's needed.

---

## Why 300 DPI

300 DPI is the standard minimum for OCR accuracy. At lower resolutions
(e.g. 150 DPI), characters blur and Tesseract accuracy drops significantly
— especially for smaller fonts and dense text. Higher DPI (e.g. 600) gives
diminishing accuracy returns while multiplying image size and processing
time. 300 is the standard recommendation from both Tesseract's own
documentation and OCR literature.

---

## Graceful degradation

If OCR fails for any reason (Tesseract not installed, Poppler missing, corrupt
page image), the exception is caught silently and the page is skipped — same
behavior as a text-based page with no content. This means:

- Text-based PDFs: unaffected, OCR code never runs
- Scanned PDFs with OCR available: all pages extracted
- Scanned PDFs without OCR: zero pages, upload rejected with 422 (same as
  original behavior, same clear error)
- Partially scanned PDFs (mix of text and image pages): text pages extracted
  directly, image pages via OCR, all in one pass

---

## System dependencies

OCR requires two system-level installs beyond pip packages:

| Tool | Purpose | Windows install |
|---|---|---|
| Tesseract | OCR engine | UB-Mannheim installer, default path `C:\Program Files\Tesseract-OCR\` |
| Poppler | PDF-to-image conversion | Extract to `C:\Program Files\poppler-x.x.x\`, note `Library\bin\` path |

And two pip packages:

```bash
pip install pytesseract pdf2image
```

`OCR_AVAILABLE` is set to `False` if either pip package is missing, so the
loader degrades gracefully without crashing on import.

---

## Verification performed

### Test 1 — Scanned PDF (15-page image-based document)

Uploaded `final_unit_3.pdf` — a 15-page scanned document with no embedded
text layer. Previously rejected with "no extractable text."

**Expected:** OCR extracts text from each page, document ingested successfully.

**Result:** ✅ `200 OK`, `chunks_stored: 15`. All 15 pages processed via OCR.

### Test 2 — Query against OCR-extracted content

Sent `POST /query/` with `{"query": "What are the types of BI architecture?"}`.

**Expected:** Retriever finds relevant chunks from OCR-extracted text.

**Result:** ✅ Retrieved 5 chunks, `confidence: medium, best match: 0.55`.
Generation blocked by Gemini quota exhaustion (429) — not an OCR issue.
Retrieval and confidence signaling both confirmed working.

### Test 3 — Text-based PDF unaffected

Uploaded `Transformers_Overview_3_Pages.pdf` — digitally created, real
embedded text.

**Expected:** Extracted via fast path (no OCR), 3 pages.

**Result:** ✅ 3 pages extracted correctly via direct text extraction.
OCR code never called.

### Test 4 — OCR chain verified in isolation

```python
from pdf2image import convert_from_path
import pytesseract

imgs = convert_from_path(
    r'C:\Users\sharm\Downloads\final_unit_3.pdf',
    first_page=1, last_page=1, dpi=300,
    poppler_path=r'C:\Program Files\poppler-26.02.0\Library\bin'
)
text = pytesseract.image_to_string(imgs[0])
print(text[:300])  # confirmed readable text output
```

**Result:** ✅ Readable text extracted from page 1 of the scanned document.

---

## Takeaway

`load_pdf()` now handles both text-based and scanned PDFs transparently —
the calling code (upload endpoint, chunker) doesn't need to know which path
was taken. The output shape is identical regardless: a list of
`{"page_number": int, "locator_type": "page", "text": str}` dicts, one per
page with extractable content.

This is worth calling out in an interview as a real production consideration:
scanned documents are extremely common in enterprise contexts (contracts,
reports, legacy documents), and a RAG system that silently rejects them is
much less useful than one that handles them transparently.
