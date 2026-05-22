import type {
  DashboardBidItem,
  DashboardListResponse,
  DashboardOpportunityItem,
  DashboardResultItem,
  DashboardSummaryResponse,
  PaperBiddingSummaryResponse
} from "./types";

const TOKEN_KEY = "bid-vector-dashboard-token";
const USERNAME_KEY = "bid-vector-dashboard-username";

export interface SessionResponse {
  access_token: string;
  token_type: string;
  operator_id?: number;
  username?: string;
}

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export function getStoredToken(): string | null {
  return window.localStorage.getItem(TOKEN_KEY);
}

export function getStoredUsername(): string | null {
  return window.localStorage.getItem(USERNAME_KEY);
}

export function storeSession(session: SessionResponse): void {
  window.localStorage.setItem(TOKEN_KEY, session.access_token);
  if (session.username) {
    window.localStorage.setItem(USERNAME_KEY, session.username);
  }
}

export function clearSession(): void {
  window.localStorage.removeItem(TOKEN_KEY);
  window.localStorage.removeItem(USERNAME_KEY);
}

export async function login(username: string, password: string): Promise<SessionResponse> {
  const response = await fetch("/api/v1/auth/session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password })
  });

  if (!response.ok) {
    throw new ApiError(response.status, "로그인에 실패했습니다.");
  }

  return response.json() as Promise<SessionResponse>;
}

export async function resetPassword(username: string, resetToken: string, newPassword: string): Promise<SessionResponse> {
  const response = await fetch("/api/v1/auth/password-reset", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, reset_token: resetToken, new_password: newPassword })
  });

  if (!response.ok) {
    throw new ApiError(response.status, "비밀번호 초기화에 실패했습니다.");
  }

  return response.json() as Promise<SessionResponse>;
}

export async function fetchDashboardSummary(token: string): Promise<DashboardSummaryResponse> {
  return request<DashboardSummaryResponse>("/api/v1/dashboard/summary", token);
}

export async function fetchOpportunities(token: string): Promise<DashboardListResponse<DashboardOpportunityItem>> {
  return request<DashboardListResponse<DashboardOpportunityItem>>("/api/v1/dashboard/opportunities", token);
}

export async function fetchBids(token: string): Promise<DashboardListResponse<DashboardBidItem>> {
  return request<DashboardListResponse<DashboardBidItem>>("/api/v1/dashboard/bids", token);
}

export async function fetchResults(token: string): Promise<DashboardListResponse<DashboardResultItem>> {
  return request<DashboardListResponse<DashboardResultItem>>("/api/v1/dashboard/results", token);
}

export async function fetchPaperBiddingSummary(token: string): Promise<PaperBiddingSummaryResponse> {
  return request<PaperBiddingSummaryResponse>("/api/v1/backtests/paper-bidding/summary", token);
}

async function request<T>(path: string, token: string): Promise<T> {
  const response = await fetch(path, {
    headers: { Authorization: `Bearer ${token}` }
  });

  if (!response.ok) {
    const message = response.status === 401 ? "세션이 만료되었습니다." : "데이터를 불러오지 못했습니다.";
    throw new ApiError(response.status, message);
  }

  return response.json() as Promise<T>;
}
