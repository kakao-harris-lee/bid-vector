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

export function fetchDashboardSummary(token?: string | null): Promise<DashboardSummaryResponse> {
  return wrapError(apiRequest<DashboardSummaryResponse>("/api/v1/dashboard/summary", { token }));
}

export function fetchOpportunities(
  token?: string | null
): Promise<DashboardListResponse<DashboardOpportunityItem>> {
  return wrapError(
    apiRequest<DashboardListResponse<DashboardOpportunityItem>>(
      "/api/v1/dashboard/opportunities",
      { token }
    )
  );
}

export function fetchBids(token?: string | null): Promise<DashboardListResponse<DashboardBidItem>> {
  return wrapError(
    apiRequest<DashboardListResponse<DashboardBidItem>>("/api/v1/dashboard/bids", { token })
  );
}

export function fetchResults(
  token?: string | null
): Promise<DashboardListResponse<DashboardResultItem>> {
  return wrapError(
    apiRequest<DashboardListResponse<DashboardResultItem>>("/api/v1/dashboard/results", { token })
  );
}

export function fetchPaperBiddingSummary(
  token?: string | null
): Promise<PaperBiddingSummaryResponse> {
  return wrapError(
    apiRequest<PaperBiddingSummaryResponse>("/api/v1/backtests/paper-bidding/summary", { token })
  );
}
