import { apiClient } from "@/lib/api-client";
import type { QueryResponse } from "@/types";

/**
 * POST /query/
 *
 * Matches query.py exactly. Note the trailing slash — FastAPI's
 * router prefix is "/query" and the route is "/", so the real path
 * is POST /query/. Dropping the slash causes a 307 redirect on most
 * FastAPI setups, which axios follows transparently but which still
 * costs a round trip — worth keeping correct rather than relying on
 * the redirect.
 */
export async function submitQuery(query: string): Promise<QueryResponse> {
  const { data } = await apiClient.post<QueryResponse>("/query/", { query });
  return data;
}
