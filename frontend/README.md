# DocMind — Frontend

The React workspace for DocMind. A two-panel UI: document management on the left, an AI chat assistant on the right. Connects to the FastAPI backend for document ingestion and RAG-powered queries.

---

## Stack

| | |
|---|---|
| Framework | React 18 + TypeScript |
| Build tool | Vite |
| Routing | TanStack Router (file-based) |
| Styling | Tailwind CSS v4 |
| Animations | Framer Motion |
| Components | shadcn/ui |
| Icons | Lucide React |

---

## Setup

```bash
# From the repo root
cd frontend

npm install
npm run dev
# → http://localhost:5173
```

The frontend expects the backend running at `http://127.0.0.1:8000` by default. To point it elsewhere, set an environment variable:

```bash
# .env.local
VITE_DOCMIND_API_BASE=http://your-backend-url
```

---

## Routes

```
/          → Hero / landing page
/workspace → Main application (upload + chat)
```

---

## Project structure

```
frontend/
├── src/
│   ├── routes/
│   │   ├── index.tsx        # Landing / hero page
│   │   └── workspace.tsx    # Main workspace — all app logic lives here
│   ├── lib/
│   │   └── utils.ts         # cn() helper (clsx + tailwind-merge)
│   └── main.tsx             # Router setup, app entry point
├── public/
├── index.html
├── vite.config.ts
├── tailwind.config.ts
└── tsconfig.json
```

---

## What `workspace.tsx` does

Everything application-specific lives in one file, split into focused components:

### `WorkspacePage`
Root layout. Holds the video background, header (logo + backend status pill + avatar), and mounts the two panels. Polls `GET /documents/` every 15 seconds as a liveness check.

### `LeftPanel` — Document Center
- Drag-and-drop upload zone (built as a `<label>` wrapping a hidden `<input type="file">`, not a bare `div` — fully accessible and clickable)
- Upload progress bar via `XMLHttpRequest` — `fetch` doesn't expose upload progress events
- Per-document `Indexed / Indexing…` status badge
- Per-document delete with spinner while the request is in flight
- Clear All with a confirmation step before the destructive action
- Listens for `docmind:highlight` custom events from the chat panel and highlights the relevant document card when citations are opened

### `RightPanel` — Interactive Assistant
- Chat history with user and assistant message bubbles, Framer Motion entry animations
- Animated typing indicator while awaiting a backend response
- **Confidence badge** — `high / medium / low` with emerald/amber/rose color coding, spring-animated on entry
- **Citations panel** — collapsible per-message. Each citation shows filename + page number. Opening it dispatches `docmind:highlight` to the left panel, which highlights and scrolls to the matching document card
- Quick-prompt chips for common queries
- `Enter` to send

---

## API calls

All requests go to `VITE_DOCMIND_API_BASE` (default `http://127.0.0.1:8000`).

| Action | Method + Path | Notes |
|---|---|---|
| Load documents on mount | `GET /documents/` | Also used as backend liveness check |
| Upload file | `POST /documents/upload` | `multipart/form-data`, progress tracked via XHR |
| Delete document | `DELETE /documents/:filename` | Per-document |
| Delete all | `DELETE /documents/` | Falls back to individual deletes if bulk endpoint unavailable |
| Send query | `POST /query/` | Returns `answer`, `citations`, `confidence`, `route` |

### Query response shape

```ts
{
  answer: string
  citations: Array<{
    source_file: string
    page_number: number | string
    locator_type: "page" | "paragraph_index"
    excerpt: string
  }>
  confidence: {
    level: "high" | "medium" | "low"
    reason: string
  }
  route: "retrieve" | "full_document" | "no_retrieval"
}
```

---

## Implementation notes

**`genId()` instead of `crypto.randomUUID()`** — `crypto.randomUUID()` requires a secure context (HTTPS or localhost on some browsers). The dev server runs on plain `http://`, so a `Math.random() + Date.now()` fallback is used for generating React list keys. Collision-resistant enough for UI purposes.

**Citation cross-panel communication via DOM events** — when the user opens the citations panel on a message, the component dispatches `window.dispatchEvent(new CustomEvent("docmind:highlight", { detail: { sources } }))`. The left panel has a matching listener that finds and highlights the matching document cards. This keeps the two panels fully decoupled — no prop drilling, no shared state, no context.

**XHR for upload progress** — `fetch` resolves only after the full response is received and doesn't expose `onprogress` for the upload phase. `XMLHttpRequest.upload.onprogress` does, which is why the upload zone uses XHR wrapped in a Promise.

**No `backdrop-blur` on any panel** — removed from all components so the video background stays fully visible. The glass effect is achieved through low-opacity backgrounds (`bg-white/[0.015]`) alone.

---

## Visual design

Dark glass aesthetic over a looping ambient video background. Key choices:

- **Violet accent** (`violet-400 / violet-500`) — send button, drag-over glow, citation badges, highlighted document cards
- **Transparent panels** — `bg-white/[0.015]` and `bg-white/[0.01]` rather than solid backgrounds
- **No backdrop blur** — keeps the video visible at full fidelity
- **Confidence colors** — emerald for high, amber for medium, rose for low — consistent with the backend status pill in the header

---

## Connecting to a different backend

Set `VITE_DOCMIND_API_BASE` in `.env.local`:

```bash
VITE_DOCMIND_API_BASE=https://your-deployed-backend.com
```

CORS must be enabled on the backend for the frontend's origin. In development the backend allows `*` — tighten this for production.