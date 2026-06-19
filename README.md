# DocMind

A multi-document RAG (Retrieval-Augmented Generation) system that lets you query across multiple documents with cited, source-grounded answers.

> 🚧 **Status: In active development.** This README is updated as each phase ships. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the live system design and the reasoning behind key decisions.

## Why DocMind

Most RAG demos answer questions but don't show their work. DocMind is built around three things a lot of student RAG projects skip:

- **Source-cited answers** — every response points back to the exact document and chunk it came from
- **Confidence signaling** — if retrieval quality is weak, DocMind says so instead of confidently guessing
- **Query routing** — not every question needs a vector search (e.g. "summarize document 2" vs. "what did the Q3 report say about churn"); DocMind decides which strategy a query actually needs

## Architecture

```
React frontend → FastAPI backend → ingestion/chunking → ChromaDB (persistent)
→ retrieval + query routing → LLM (Anthropic API) → cited response
```

Full design rationale lives in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Tech Stack

- **Backend:** FastAPI, ChromaDB (persistent client), Anthropic API
- **Frontend:** React
- **Core techniques:** document chunking, embedding-based retrieval, query routing, citation extraction

## Project Status

- [ ] Phase 1 — Document ingestion & chunking
- [ ] Phase 2 — Retrieval, query routing & generation
- [ ] Phase 3 — API layer
- [ ] Phase 4 — Frontend
- [ ] Phase 5 — Deployment (HuggingFace Spaces)

## Setup

_Instructions will be added as the backend stabilizes (Phase 1)._

## License

MIT — see [LICENSE](LICENSE)
