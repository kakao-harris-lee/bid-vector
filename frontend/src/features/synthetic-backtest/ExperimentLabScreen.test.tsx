import { act, fireEvent, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderApp } from "@/test-utils";
import { toastApi } from "@/shared/components/ui";
import type { DashboardSummaryResponse } from "@/shared/types";
import type {
  SyntheticExperimentParams,
  SyntheticExperimentSampleGapPlanResponse,
  SyntheticExperimentSampleGapRunCandidateResponse
} from "@/shared/types/synthetic";

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

const experimentParams: SyntheticExperimentParams = {
  start_at: "2025-01-01T00:00:00+00:00",
  end_at: "2025-12-31T23:59:59+00:00",
  category: "software",
  limit: 200,
  scenario: "base",
  settle_actions: false
};

const sampleGapPlan: SyntheticExperimentSampleGapPlanResponse = {
  generated_at: "2026-06-18T00:00:00Z",
  max_runs: 20,
  scanned_completed_run_count: 1,
  source_run_count: 1,
  legacy_summary_run_count: 0,
  gap_count: 1,
  warnings: [],
  gaps: [
    {
      priority: 1,
      dimension: "category",
      key: "software",
      settled_count: 18,
      sample_target: 30,
      missing_settled_count: 12,
      total_missing_settled_count: 12,
      source_run_count: 1,
      related_preset_names: ["g1-software-base-12m"],
      related_run_ids: [9],
      related_runs: [],
      warnings: [],
      recommendation: {
        preset_name: "g1-software-base-12m",
        params: { category: "software", limit: 200 },
        actions: [
          {
            code: "rerun_related_preset",
            label: "Rerun related preset",
            detail: "Repeat the related synthetic experiment preset."
          }
        ]
      }
    }
  ]
};

const baseCandidate: SyntheticExperimentSampleGapRunCandidateResponse = {
  generated_at: "2026-06-18T00:00:00Z",
  gap: sampleGapPlan.gaps![0],
  action_code: "rerun_related_preset",
  action_label: "Rerun related preset",
  preset_name: "g1-software-base-12m",
  params: experimentParams,
  operator_slugs: ["sw-small-seoul", "sw-missing-busan"],
  experiment_payload: {
    name: "g1-software-base-12m",
    description: "Sample-gap follow-up candidate for category:software.",
    params: experimentParams,
    operator_slugs: ["sw-small-seoul", "sw-missing-busan"]
  },
  experiment_id: 7,
  latest_run_id: 9,
  latest_run_status: "completed",
  next_step: "run_existing_experiment",
  run_allowed: true,
  blocked_by_warnings: [],
  warnings: [],
  message: "Existing experiment is ready to select and run asynchronously."
};

function jsonResponse(payload: unknown, status = 200): Promise<Response> {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(payload),
    headers: { get: () => null }
  } as unknown as Response);
}

function stubExperimentLab(candidate: SyntheticExperimentSampleGapRunCandidateResponse) {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/api/v1/dashboard/summary")) return jsonResponse(emptySummary);
    if (url === "/api/v1/operator/accounts") return jsonResponse(privilegedAccounts);
    if (url === "/api/v1/synthetic/experiments/presets") return jsonResponse({ presets: [] });
    if (url === "/api/v1/synthetic/experiments") return jsonResponse([]);
    if (url === "/api/v1/synthetic/experiments/sample-gaps?max_runs=20") {
      return jsonResponse(sampleGapPlan);
    }
    if (
      url === "/api/v1/synthetic/experiments/sample-gaps/candidates" &&
      init?.method === "POST"
    ) {
      return jsonResponse(candidate);
    }
    return jsonResponse({}, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
}

beforeEach(() => {
  window.localStorage.setItem("bid-vector-dashboard-token", "token-synthetic");
  window.history.pushState({}, "", "/admin/synthetic-backtest");
  vi.restoreAllMocks();
  act(() => {
    toastApi.clearAll();
  });
});

describe("ExperimentLabScreen sample-gap safety visibility", () => {
  it("shows unresolved operator target and disables selection when operator scope is not ready", async () => {
    stubExperimentLab({
      ...baseCandidate,
      operator_id_scope_ready: false,
      operator_targets: [
        {
          slug: "sw-small-seoul",
          username: "synthetic-sw-small-seoul",
          operator_id: 101,
          user_id: 101,
          resolved: true,
          operator_id_scope_ready: true
        },
        {
          slug: "sw-missing-busan",
          username: "synthetic-sw-missing-busan",
          operator_id: null,
          user_id: null,
          resolved: false,
          operator_id_scope_ready: false
        }
      ]
    });

    renderApp();

    expect(await screen.findByLabelText("sample-gap 실행 후보")).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "Rerun related preset" }));

    const panel = await screen.findByLabelText("선택된 sample-gap 후보");
    expect(within(panel).getByText("operator_id_scope_ready")).toBeInTheDocument();
    expect(within(panel).getByText("false")).toBeInTheDocument();
    expect(within(panel).getByText("run_allowed")).toBeInTheDocument();
    expect(within(panel).getByText("true")).toBeInTheDocument();
    expect(within(panel).getByText(/operator_id scope가 준비되지 않았습니다/)).toBeInTheDocument();
    expect(within(panel).getByText("sw-missing-busan")).toBeInTheDocument();
    expect(within(panel).getByText(/synthetic-sw-missing-busan/)).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "기존 실험 선택" })).toBeDisabled();
  });

  it("keeps mixed-data warnings destructive and prevents approval actions from being enabled", async () => {
    stubExperimentLab({
      ...baseCandidate,
      blocked_by_warnings: ["canonical_synthetic_mixed"],
      message: "canonical_synthetic_mixed warning blocks execution until synthetic-only cleanup."
    });

    renderApp();

    expect(await screen.findByLabelText("sample-gap 실행 후보")).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "Rerun related preset" }));

    const panel = await screen.findByLabelText("선택된 sample-gap 후보");
    expect(within(panel).getByText("blocked_by_warnings")).toBeInTheDocument();
    expect(within(panel).getByText("canonical_synthetic_mixed")).toBeInTheDocument();
    expect(
      within(panel).getByText(/canonical_synthetic_mixed warning blocks execution/i)
    ).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "기존 실험 선택" })).toBeDisabled();
  });
});
