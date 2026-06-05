import { apiRequest } from "./client";
import { ApiError } from "./session";
import type {
  DashboardBidItem,
  DashboardListResponse,
  DashboardOpportunityItem,
  DashboardResultItem,
  DashboardSummaryResponse,
  PaperBiddingSummaryResponse
} from "@/shared/types";

function wrapError<T>(promise: Promise<T>): Promise<T> {
  return promise.catch((err) => {
    if (err instanceof ApiError && err.status !== 401) {
      throw new ApiError(err.status, "데이터를 불러오지 못했습니다.");
    }
    throw err;
  });
}

function withOperator(path: string, operatorId?: number | null): string {
  if (typeof operatorId !== "number") return path;
  const separator = path.includes("?") ? "&" : "?";
  return `${path}${separator}operator_id=${operatorId}`;
}

export function fetchDashboardSummary(
  token?: string | null,
  operatorId?: number | null
): Promise<DashboardSummaryResponse> {
  return wrapError(
    apiRequest<DashboardSummaryResponse>(
      withOperator("/api/v1/dashboard/summary", operatorId),
      { token }
    )
  );
}

export function fetchOpportunities(
  token?: string | null,
  operatorId?: number | null
): Promise<DashboardListResponse<DashboardOpportunityItem>> {
  return wrapError(
    apiRequest<DashboardListResponse<DashboardOpportunityItem>>(
      withOperator("/api/v1/dashboard/opportunities", operatorId),
      { token }
    )
  );
}

export function fetchBids(
  token?: string | null,
  operatorId?: number | null
): Promise<DashboardListResponse<DashboardBidItem>> {
  return wrapError(
    apiRequest<DashboardListResponse<DashboardBidItem>>(
      withOperator("/api/v1/dashboard/bids", operatorId),
      { token }
    )
  );
}

export function fetchResults(
  token?: string | null,
  operatorId?: number | null
): Promise<DashboardListResponse<DashboardResultItem>> {
  return wrapError(
    apiRequest<DashboardListResponse<DashboardResultItem>>(
      withOperator("/api/v1/dashboard/results", operatorId),
      { token }
    )
  );
}

export function fetchPaperBiddingSummary(
  token?: string | null
): Promise<PaperBiddingSummaryResponse> {
  return wrapError(
    apiRequest<PaperBiddingSummaryResponse>("/api/v1/backtests/paper-bidding/summary", { token })
  );
}
