import { apiRequest } from "./client";
import { ApiError } from "./session";
import type {
  OperatorProfileResponse,
  OperatorProfileUpdatePayload
} from "@/shared/types/profile";

function wrap<T>(promise: Promise<T>, fallback: string): Promise<T> {
  return promise.catch((err) => {
    if (err instanceof ApiError && err.status !== 401) {
      throw new ApiError(err.status, fallback);
    }
    throw err;
  });
}

export function fetchProfile(token?: string | null): Promise<OperatorProfileResponse> {
  return wrap(
    apiRequest<OperatorProfileResponse>("/api/v1/operator/profile", { token }),
    "업체 정보를 불러오지 못했습니다."
  );
}

export function updateProfile(
  payload: OperatorProfileUpdatePayload,
  token?: string | null
): Promise<OperatorProfileResponse> {
  return wrap(
    apiRequest<OperatorProfileResponse>("/api/v1/operator/profile", {
      method: "PUT",
      body: payload,
      token
    }),
    "업체 정보 저장에 실패했습니다."
  );
}
