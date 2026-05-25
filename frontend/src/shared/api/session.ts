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
