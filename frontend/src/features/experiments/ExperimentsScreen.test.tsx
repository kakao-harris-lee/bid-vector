import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderApp } from "@/test-utils";
import { toastApi } from "@/shared/components/ui";
import type { DashboardSummaryResponse } from "@/shared/types";
import type {
  DecisionExperimentRunListResponse,
  DecisionExperimentThresholdApplyResponse
} from "@/shared/types/experiments";

const emptySummary: DashboardSummaryResponse = {
  operator_id: 1,
  generated_at: "2026-05-19T00:00:00Z",
  today: "2026-05-19",
  operational_status: {
    key: "x",
    label: "운영 상태",
    value: "completed",
    unit: "state",
    status: "healthy",
    detail: ""
  },
  metrics: [],
  work_items: [],
  sections: [
    { key: "opportunities", label: "입찰", count: 0, status: "healthy", href: "/dashboard/opportunities" },
    { key: "bids", label: "투찰", count: 0, status: "healthy", href: "/dashboard/bids" },
    { key: "results", label: "결과", count: 0, status: "healthy", href: "/dashboard/results" }
  ],
  recent_opportunities: [],
  recent_bids: [],
  recent_results: [],
  realtime_href: "/api/v1/realtime/events"
};

const privilegedAccounts = {
  current_operator_id: 1,
  current_operator_username: "operator",
  is_privileged: true,
  operator_count: 1,
  operators: [
    {
      operator_id: 1,
      username: "operator",
      full_name: "본사 운영자",
      company: "본사",
      business_type: null,
      is_canonical: true,
      is_synthetic: false,
      is_active: true,
      profile_configured: true
    }
  ]
};

const experimentRun = {
  id: 7,
  operator_id: 1,
  experiment_key: "exp-1",
  recommendation_key: "rec-1",
  status: "completed" as const,
  outcome: "success" as const,
  priority_rank: 1,
  title: "임계값 조정 실험",
  hypothesis: "review_threshold를 0.05 낮춰 제출률 개선",
  suggested_change: "review_threshold -0.05",
  target_metric: "overall_submission_rate",
  success_criteria: "+5%p",
  notes: null,
  started_at: "2026-05-01T00:00:00Z",
  ended_at: null,
  last_evaluated_at: "2026-05-18T00:00:00Z",
  created_at: "2026-05-01T00:00:00Z",
  updated_at: "2026-05-18T00:00:00Z",
  supported_apply_types: ["thresholds" as const],
  applied_apply_types: [] as Array<"thresholds" | "strategy">,
  application_status: "ready" as const,
  application_detail: "ready",
  review_bucket: "ready_to_apply" as const,
  review_priority: 9,
  review_reason: "ready"
};

const listResponse: DecisionExperimentRunListResponse = {
  operator_id: 1,
  result_count: 1,
  total_match_count: 1,
  sort: "needs_attention",
  active_count: 0,
  completed_count: 1,
  rolled_back_count: 0,
  failed_count: 0,
  success_count: 1,
  pending_count: 0,
  ready_to_apply_count: 1,
  applied_count: 0,
  runs: [experimentRun]
};

const dryRunResponse: DecisionExperimentThresholdApplyResponse = {
  operator_id: 1,
  run_id: experimentRun.id,
  experiment_key: experimentRun.experiment_key,
  recommendation_key: experimentRun.recommendation_key,
  applied: false,
  dry_run: true,
  latest_outcome: "success",
  threshold_updates: [
    {
      parameter: "review_threshold",
      label: "검토 임계값",
      direction: "decrease",
      previous_value: 0.5,
      suggested_value: 0.45,
      delta: -0.05,
      rationale: "최근 4주 데이터 기준 개선 예상"
    }
  ],
  strategy_thresholds: { bid_now_threshold: 0.7, review_threshold: 0.5 },
  detail: "dry-run 결과: 적용 시 review_threshold가 0.45로 변경됩니다."
};

function jsonResponse(payload: unknown, status = 200): Promise<Response> {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(payload),
    headers: { get: () => null }
  } as unknown as Response);
}

beforeEach(() => {
  window.localStorage.setItem("bid-vector-dashboard-token", "token-experiments");
  window.history.pushState({}, "", "/admin/experiments");
  vi.restoreAllMocks();
  act(() => {
    toastApi.clearAll();
  });
});

describe("ExperimentsScreen", () => {
  it("Dry-run 결과를 확인한 뒤에만 Force 적용 버튼이 활성화된다", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/v1/dashboard/summary")) return jsonResponse(emptySummary);
      if (url === "/api/v1/operator/accounts") return jsonResponse(privilegedAccounts);
      if (url.startsWith("/api/v1/analytics/decision-experiments?")) return jsonResponse(listResponse);
      if (url === `/api/v1/analytics/decision-experiments/${experimentRun.id}/apply-thresholds`) {
        const body = init?.body ? JSON.parse(String(init.body)) : {};
        if (body.dry_run) return jsonResponse(dryRunResponse);
        return jsonResponse({ ...dryRunResponse, applied: true, dry_run: false, detail: "적용 완료" });
      }
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    expect(
      await screen.findByRole("heading", { name: "실험 lifecycle", level: 2 })
    ).toBeInTheDocument();
    const runButton = await screen.findByRole("button", { name: /임계값 조정 실험/ });
    fireEvent.click(runButton);

    const forceButton = await screen.findByRole("button", { name: "Force 적용" });
    expect(forceButton).toBeDisabled();

    const dryRunButton = screen.getByRole("button", { name: "Dry-run" });
    fireEvent.click(dryRunButton);

    expect(await screen.findByText("Dry-run diff")).toBeInTheDocument();
    await waitFor(() => expect(forceButton).not.toBeDisabled());
  });

  it("Force 적용이 실패하면 danger toast가 표시된다", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/v1/dashboard/summary")) return jsonResponse(emptySummary);
      if (url === "/api/v1/operator/accounts") return jsonResponse(privilegedAccounts);
      if (url.startsWith("/api/v1/analytics/decision-experiments?")) return jsonResponse(listResponse);
      if (url === `/api/v1/analytics/decision-experiments/${experimentRun.id}/apply-thresholds`) {
        const body = init?.body ? JSON.parse(String(init.body)) : {};
        if (body.dry_run) return jsonResponse(dryRunResponse);
        return jsonResponse({ detail: "force apply rejected" }, 400);
      }
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    const runButton = await screen.findByRole("button", { name: /임계값 조정 실험/ });
    fireEvent.click(runButton);

    fireEvent.click(screen.getByRole("button", { name: "Dry-run" }));
    expect(await screen.findByText("Dry-run diff")).toBeInTheDocument();
    const force = await screen.findByRole("button", { name: "Force 적용" });
    await waitFor(() => expect(force).not.toBeDisabled());

    fireEvent.click(force);

    expect(await screen.findByText("임계값 적용 실패")).toBeInTheDocument();
  });
});
