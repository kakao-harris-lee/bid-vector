import { apiRequest } from "./client";
import { ApiError } from "./session";
import type {
  PaperBiddingRunExecutionResponse,
  PaperBiddingRunRequestPayload
} from "@/shared/types/profile";

function wrap<T>(promise: Promise<T>, fallback: string): Promise<T> {
  return promise.catch((err) => {
    if (err instanceof ApiError && err.status !== 401) {
      throw new ApiError(err.status, fallback);
    }
    throw err;
  });
}

/**
 * Run an on-demand historical paper-bidding backtest for the canonical
 * operator. Bearer auth is required by the backend route. `persist` defaults to
 * false so the "이 프로필로 백테스트" loop does not write run records.
 */
export function runProfileBacktest(
  payload: PaperBiddingRunRequestPayload,
  token?: string | null
): Promise<PaperBiddingRunExecutionResponse> {
  return wrap(
    apiRequest<PaperBiddingRunExecutionResponse>("/api/v1/backtests/paper-bidding/runs", {
      method: "POST",
      body: payload,
      token
    }),
    "백테스트 실행에 실패했습니다."
  );
}
