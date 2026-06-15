import { apiRequest } from "./client";
import { ApiError, getStoredToken } from "./session";
import type { BidFormDraftResponse } from "@/shared/types/bidFormDraft";

function wrap<T>(promise: Promise<T>, fallback: string): Promise<T> {
  return promise.catch((err) => {
    if (err instanceof ApiError && err.status !== 401) {
      throw new ApiError(err.status, fallback);
    }
    throw err;
  });
}

function basePath(decisionRecordId: number): string {
  return `/api/v1/operations/bid-decisions/${decisionRecordId}/bid-form-draft`;
}

/**
 * GET /api/v1/operations/bid-decisions/{decisionRecordId}/bid-form-draft (json)
 *
 * Maps one persisted `BidDecisionRecord` onto the 나라장터 투찰서 입력 항목 so the
 * operator can read the draft and **type the values into KONEPS by hand**. 자동
 * 제출은 하지 않는다. 404 when the record id is unknown / belongs to a different
 * operator / the linked project is missing — translated to a Korean message.
 */
export function fetchBidFormDraft(
  decisionRecordId: number,
  token?: string | null
): Promise<BidFormDraftResponse> {
  return wrap(
    apiRequest<BidFormDraftResponse>(`${basePath(decisionRecordId)}?format=json`, {
      token
    }),
    "투찰서 초안을 불러오지 못했습니다."
  );
}

/**
 * GET …/bid-form-draft?format=csv|text — returns the backend's exact CSV/text
 * render as a string.
 *
 * `apiRequest` always JSON-parses the response body, so we `fetch` directly here
 * (mirroring its Bearer header + 401 session-expired dispatch minimally) and
 * return `response.text()`. Reusing the backend render keeps the CSV/text output
 * free of any client-side drift.
 */
export async function fetchBidFormDraftRaw(
  decisionRecordId: number,
  format: "csv" | "text",
  token?: string | null
): Promise<string> {
  const path = `${basePath(decisionRecordId)}?format=${format}`;
  const authToken = token ?? getStoredToken();
  const headers: Record<string, string> = {};
  if (authToken) headers.Authorization = `Bearer ${authToken}`;

  const response = await fetch(path, { method: "GET", headers });

  if (!response.ok) {
    if (response.status === 401 && typeof window !== "undefined") {
      window.dispatchEvent(new Event("bid-vector:session-expired"));
    }
    throw new ApiError(response.status, "투찰서 초안을 불러오지 못했습니다.");
  }

  return response.text();
}
