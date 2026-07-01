// Types mirror the actual backend output shapes documented in:
// testing-retriever.md, testing-generator.md, testing-confidence.md, testing-loaders.md

export type LocatorType = "page" | "paragraph_index";

export type ConfidenceLevel = "high" | "medium" | "low";

export interface Citation {
  source_file: string;
  page_number: number;
  locator_type: LocatorType;
  excerpt: string;
}

export interface ConfidenceAssessment {
  level: ConfidenceLevel;
  reason: string;
}

export type QueryRoute = "retrieve" | "full_document" | "no_retrieval";

// Matches the actual /query response shape in query.py exactly —
// including `query` (echoed input) and `route`, which the UI can use
// to explain *why* zero citations came back (e.g. a no_retrieval route
// vs. a genuine empty-result search) rather than treating all
// zero-citation answers as the same case.
export interface QueryResponse {
  query: string;
  route: QueryRoute;
  answer: string;
  citations: Citation[];
  confidence: ConfidenceAssessment;
}

// query.py's GenerationError path returns this as the `detail` field
// of a 500 HTTPException — a structured object, not a plain string.
// Confidence still arrives here even though generation failed, by
// design (see query.py's module docstring). The API client needs to
// detect and unwrap this shape specially; a generic string-detail
// error handler will silently produce "[object Object]" otherwise.
export interface GenerationFailureDetail {
  error: string;
  confidence: ConfidenceAssessment;
}

export type IngestionStatus = "indexing" | "indexed" | "failed";

// GET /documents/ returns ONLY filenames — no size, no timestamp, no
// chunk count. The backend discards the uploaded file after ingestion;
// nothing about it persists except the ChromaDB chunks themselves.
export interface DocumentsListResponse {
  documents: string[];
  count: number;
}

// GET /documents/status — keyed by sanitised filename, in-memory only
// on the backend (resets on server restart). A filename can appear
// here without yet appearing in DocumentsListResponse (still indexing),
// or appear in DocumentsListResponse with no status entry at all (was
// ingested in a previous server session, before the current restart).
export interface DocumentsStatusResponse {
  status: Record<string, IngestionStatus>;
}

// Frontend-only composed shape: the join of the two endpoints above,
// by filename. `status` is undefined when a file exists in ChromaDB
// but has no status record (pre-restart upload) — the UI must treat
// that as "indexed" (it's present in the list, so it succeeded),
// not as "unknown/failed".
export interface DocumentRecord {
  filename: string;
  status: IngestionStatus | undefined;
}

export interface UploadResponse {
  message: string;
  filename: string;
  status: "indexing";
}

export interface DeleteDocumentResponse {
  message: string;
  filename: string;
  chunks_deleted: number;
}

export interface ClearDocumentsResponse {
  message: string;
  chunks_deleted: number;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
  confidence?: ConfidenceAssessment;
  createdAt: string;
  isStreaming?: boolean;
}

export interface Conversation {
  id: string;
  title: string;
  messages: ChatMessage[];
  createdAt: string;
  updatedAt: string;
}

export interface HealthStatus {
  status: "ok" | "degraded" | "down";
}

export interface ApiErrorShape {
  detail: string;
  code?: string;
}
