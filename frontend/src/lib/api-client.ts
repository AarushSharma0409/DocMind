import axios, { AxiosError } from "axios";
import type { GenerationFailureDetail } from "@/types";

const baseURL = import.meta.env.VITE_API_BASE_URL;

if (!baseURL) {
  // Fail loudly at startup rather than silently hitting a relative path
  // that happens to 404 in a confusing way later.
  console.error(
    "VITE_API_BASE_URL is not set. Create a .env file from .env.example."
  );
}

export const apiClient = axios.create({
  baseURL,
  timeout: 30_000,
});

// The backend enforces X-API-Key globally via middleware (main.py),
// but ONLY if DOCMIND_API_KEY is set server-side — if it's unset there,
// auth is silently open and this header is ignored either way. The
// frontend can't know which state the backend is in without trying a
// request, so this just sends the key whenever one is configured
// client-side and omits it otherwise.
const apiKey = import.meta.env.VITE_API_KEY;

apiClient.interceptors.request.use((config) => {
  if (apiKey) {
    config.headers["X-API-Key"] = apiKey;
  }
  return config;
});

export class ApiError extends Error {
  status?: number;
  code?: string;
  // Populated only when the backend's GenerationError path fired
  // (query.py): confidence info survives a failed generation by
  // design, so the UI can still say "retrieval was weak" even when
  // there's no answer to show.
  confidence?: GenerationFailureDetail["confidence"];

  constructor(
    message: string,
    status?: number,
    code?: string,
    confidence?: GenerationFailureDetail["confidence"]
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.confidence = confidence;
  }
}

function isGenerationFailureDetail(
  detail: unknown
): detail is GenerationFailureDetail {
  return (
    typeof detail === "object" &&
    detail !== null &&
    "error" in detail &&
    "confidence" in detail
  );
}

// FastAPI's error envelope is always {detail: X}. X is a plain string
// for most errors, but query.py's GenerationError path returns X as a
// structured {error, confidence} object instead. This type describes
// the ENVELOPE, with that union living inside `detail` — not a union
// of two envelope shapes, which was the earlier (wrong) version and
// is why `.detail` didn't type-check: GenerationFailureDetail has no
// `.detail` field, it IS the detail's content.
interface FastApiErrorEnvelope {
  detail: string | GenerationFailureDetail;
}

apiClient.interceptors.response.use(
  (response) => response,
  (error: AxiosError<FastApiErrorEnvelope>) => {
    if (error.response) {
      const rawDetail = error.response.data?.detail;

      if (isGenerationFailureDetail(rawDetail)) {
        // query.py's GenerationError path: detail is {error, confidence},
        // not a string. Unwrap it rather than letting it stringify.
        throw new ApiError(
          rawDetail.error,
          error.response.status,
          "GENERATION_FAILED",
          rawDetail.confidence
        );
      }

      // main.py's require_api_key middleware: 401 with this exact
      // detail string when DOCMIND_API_KEY is set server-side and
      // either no X-API-Key header was sent or it didn't match.
      if (
        error.response.status === 401 &&
        typeof rawDetail === "string" &&
        rawDetail.includes("Invalid or missing API key")
      ) {
        throw new ApiError(
          "DocMind rejected the request: missing or incorrect API key.",
          401,
          "AUTH_REQUIRED"
        );
      }

      const detail =
        typeof rawDetail === "string" ? rawDetail : error.message;
      throw new ApiError(detail, error.response.status);
    }
    if (error.request) {
      throw new ApiError(
        "Could not reach DocMind's server. Check your connection and try again.",
        undefined,
        "NETWORK_ERROR"
      );
    }
    throw new ApiError(error.message);
  }
);