import { apiRequest } from "./client";
import type { SessionResponse } from "./session";
import { ApiError } from "./session";

export async function login(username: string, password: string): Promise<SessionResponse> {
  try {
    return await apiRequest<SessionResponse>("/api/v1/auth/session", {
      method: "POST",
      body: { username, password }
    });
  } catch (err) {
    if (err instanceof ApiError) throw new ApiError(err.status, "로그인에 실패했습니다.");
    throw err;
  }
}

export async function resetPassword(
  username: string,
  resetToken: string,
  newPassword: string
): Promise<SessionResponse> {
  try {
    return await apiRequest<SessionResponse>("/api/v1/auth/password-reset", {
      method: "POST",
      body: { username, reset_token: resetToken, new_password: newPassword }
    });
  } catch (err) {
    if (err instanceof ApiError) throw new ApiError(err.status, "비밀번호 초기화에 실패했습니다.");
    throw err;
  }
}
