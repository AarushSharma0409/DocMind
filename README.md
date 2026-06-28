---
title: DocMind
emoji: 🧠
colorFrom: purple
colorTo: indigo
sdk: docker
pinned: false
---

# DocMind

Multi-document RAG system with citations, confidence signaling, and query routing.

Upload PDF, DOCX, or TXT files and ask questions across all of them. Every answer cites the exact source document and page.

> **Note:** This demo runs on Hugging Face Spaces free tier with in-memory ChromaDB storage. Uploaded documents are cleared on Space restart. The persistent storage architecture is the default for local deployment — see the [main repo](https://github.com/AarushSharma0409/DocMind) for full details.