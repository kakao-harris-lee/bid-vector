import { apiRequest } from "./client";
import { ApiError } from "./session";
import type { components } from "@/shared/types/openapi";

export type OperatorAccountItem = components["schemas"]["OperatorAccountItem"];
export type OperatorAccountListResponse = components["schemas"]["OperatorAccountListResponse"];

function wrap<T>(promise: Promise<T>, fallback: string): Promise<T> {
  return promise.catch((err) => {
    if (err instanceof ApiError && err.status !== 401) {
      throw new ApiError(err.status, fallback);
    }
    throw err;
  });
}

/**
 * Fetch the operator-account catalogue visible to the current bearer token.
 *
 * - Privileged callers (canonical operator / admin) receive canonical + every
 *   ``synthetic-*`` row so the company-switcher dropdown can render the full
 *   list.
 * - Non-privileged callers receive only their own row; the dropdown collapses
 *   to a single self-pick and is typically hidden.
 */
export function fetchOperatorAccounts(
  token?: string | null
): Promise<OperatorAccountListResponse> {
  return wrap(
    apiRequest<OperatorAccountListResponse>("/api/v1/operator/accounts", { token }),
    "회사 목록을 불러오지 못했습니다."
  );
}
