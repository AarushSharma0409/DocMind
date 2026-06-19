# DocMind — Architecture & Design Decisions

This document is updated after every phase. It's not just a diagram — it's a record of *why* each decision was made, so it can double as interview prep later.

---

## System Overview

```
React frontend → FastAPI backend → ingestion/chunking → ChromaDB (persistent)
→ retrieval + query routing → LLM (Anthropic API) → cited response
```

---

## Phase 1 — Document Ingestion & Chunking

**Status:** Not started

_To be filled in as we build: chunking strategy chosen, chunk size/overlap, why, embedding model used, metadata schema._

---

## Phase 2 — Retrieval, Query Routing & Generation

**Status:** Not started

_To be filled in: retrieval strategy, query routing logic, citation extraction approach, confidence signaling design._

---

## Phase 3 — API Layer

**Status:** Not started

_To be filled in: endpoint design, error handling approach._

---

## Phase 4 — Frontend

**Status:** Not started

_To be filled in: UI decisions, how citations are surfaced to the user._

---

## Phase 5 — Deployment

**Status:** Not started

_To be filled in: deployment setup, any production considerations._

---

## Key Design Decisions Log

| Decision | Choice | Reasoning |
|---|---|---|
| _(filled in as we go)_ | | |
