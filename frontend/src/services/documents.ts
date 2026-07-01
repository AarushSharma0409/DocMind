import { apiClient } from "@/lib/api-client";
import type {
  DocumentsListResponse,
  DocumentsStatusResponse,
  DocumentRecord,
  UploadResponse,
  DeleteDocumentResponse,
  ClearDocumentsResponse,
} from "@/types";

/**
 * POST /documents/upload — returns 202 immediately; ingestion runs in
 * the background on the server. This function only confirms the file
 * was accepted, not that it's indexed. Callers must poll
 * fetchDocuments() afterward to learn the real outcome.
 *
 * onUploadProgress reflects bytes sent over the wire (the POST itself),
 * NOT ingestion progress — documents.py exposes no ingestion progress
 * signal, only a three-state enum via GET /documents/status. Don't
 * build a progress bar that implies more granularity than that exists.
 */
export async function uploadDocument(
  file: File,
  onUploadProgress?: (percent: number) => void
): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append("file", file);

  const { data } = await apiClient.post<UploadResponse>(
    "/documents/upload",
    formData,
    {
      headers: { "Content-Type": "multipart/form-data" },
      onUploadProgress: (event) => {
        if (onUploadProgress && event.total) {
          onUploadProgress(Math.round((event.loaded / event.total) * 100));
        }
      },
    }
  );
  return data;
}

/** GET /documents/ — flat filename list. No size, no timestamp. */
export async function fetchDocumentFilenames(): Promise<DocumentsListResponse> {
  const { data } = await apiClient.get<DocumentsListResponse>("/documents/");
  return data;
}

/**
 * GET /documents/status — per-filename ingestion state, in-memory on
 * the backend (resets on server restart). Never assume every filename
 * from fetchDocumentFilenames() has a corresponding entry here.
 */
export async function fetchIngestionStatus(): Promise<DocumentsStatusResponse> {
  const { data } = await apiClient.get<DocumentsStatusResponse>(
    "/documents/status"
  );
  return data;
}

/**
 * Composes the two endpoints above into one list the UI can render
 * directly. This join has to happen client-side — the backend has no
 * single endpoint that returns both filename and status together.
 *
 * A filename present in the list but absent from the status map is
 * treated as "indexed": if it's in ChromaDB, ingestion already
 * succeeded, even if the status record didn't survive a server
 * restart to say so explicitly.
 */
export async function fetchDocuments(): Promise<DocumentRecord[]> {
  const [listResult, statusResult] = await Promise.all([
    fetchDocumentFilenames(),
    fetchIngestionStatus(),
  ]);

  const indexedFilenames = new Set(listResult.documents);
  const records: DocumentRecord[] = listResult.documents.map((filename) => ({
    filename,
    status: statusResult.status[filename] ?? "indexed",
  }));

  // Files that are "indexing" or "failed" haven't reached ChromaDB yet,
  // so they're absent from listResult.documents entirely — without this,
  // an in-progress or failed upload would be invisible to the UI until
  // it either succeeds or the user refreshes blind.
  for (const [filename, status] of Object.entries(statusResult.status)) {
    if (!indexedFilenames.has(filename)) {
      records.push({ filename, status });
    }
  }

  return records;
}

/** DELETE /documents/{source_file} — keyed by filename, not an ID. */
export async function deleteDocument(
  filename: string
): Promise<DeleteDocumentResponse> {
  const { data } = await apiClient.delete<DeleteDocumentResponse>(
    `/documents/${encodeURIComponent(filename)}`
  );
  return data;
}

/** DELETE /documents/ — clears the entire knowledge base. */
export async function clearAllDocuments(): Promise<ClearDocumentsResponse> {
  const { data } = await apiClient.delete<ClearDocumentsResponse>(
    "/documents/"
  );
  return data;
}
