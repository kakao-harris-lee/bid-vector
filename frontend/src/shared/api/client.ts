import { ApiError, getStoredToken } from "./session";

export interface RequestOptions extends Omit<RequestInit, "headers" | "body"> {
  token?: string | null;
  body?: unknown;
  headers?: Record<string, string>;
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { token: explicitToken, body, headers = {}, method = "GET", ...rest } = options;
  const token = explicitToken ?? getStoredToken();

  const finalHeaders: Record<string, string> = { ...headers };
  if (token) finalHeaders.Authorization = `Bearer ${token}`;

  let payload: BodyInit | undefined;
  if (body !== undefined) {
    finalHeaders["Content-Type"] = finalHeaders["Content-Type"] ?? "application/json";
    payload = typeof body === "string" ? body : JSON.stringify(body);
  }

  const response = await fetch(path, { method, headers: finalHeaders, body: payload, ...rest });

  if (!response.ok) {
    const message = messageForStatus(response.status);
    throw new ApiError(response.status, message);
  }

  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

function messageForStatus(status: number): string {
  if (status === 401) return "세션이 만료되었습니다.";
  if (status === 403) return "권한이 없습니다.";
  if (status === 404) return "요청한 자원을 찾을 수 없습니다.";
  if (status >= 500) return "서버 오류가 발생했습니다.";
  return "요청을 처리하지 못했습니다.";
}

export { ApiError };
