import { act, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createTestQueryClient } from "@/test-utils";
import { Toaster, toastApi } from "@/shared/components/ui";
import type { ShellOutletContext } from "@/app/dashboardContext";
import type { OperatorStrategyCandidatesResponse } from "@/shared/types/strategy";
import { CandidatesPreview } from "./CandidatesPreview";

/** 테스트 폴링 주기 — 실제 벽시계를 쓰는 ExperimentRunProgress.test 패턴. */
const POLL_MS = 20;
const session = { token: "token-candidates", username: "operator" };
const CANDIDATE_TITLE = "서울 AI 데이터 통합 플랫폼";

function minutesAgo(minutes: number): string {
  // 버킷 경계에서 흔들리지 않게 1초 더 뒤로 민다(floor 버킷).
  return new Date(Date.now() - minutes * 60_000 - 1_000).toISOString();
}

function snapshot(
  overrides: Partial<OperatorStrategyCandidatesResponse> = {}
): OperatorStrategyCandidatesResponse {
  return {
    operator_id: 1,
    evaluated_project_count: 250,
    returned_candidate_count: 1,
    high_priority_only: false,
    candidates: [
      {
        project_id: 77,
        title: CANDIDATE_TITLE,
        category: "software",
        budget_estimate: 130_000_000,
        deadline: null,
        matched_score: 0.7,
        probability_score: 0.8,
        priority_score: 0.9,
        action: "review",
        recommended_amount: 111_000_000,
        analysis_summary: "요약",
        strategy_reasons: []
      }
    ],
    computed_at: minutesAgo(3),
    snapshot_status: "idle",
    stale: false,
    ...overrides
  };
}

const bootstrapSnapshot = snapshot({
  computed_at: null,
  snapshot_status: "running",
  candidates: [],
  returned_candidate_count: 0,
  evaluated_project_count: 0
});

function jsonResponse(payload: unknown, status = 200): Promise<Response> {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(payload)
  } as Response);
}

/** 명시 갱신 202 응답 (백엔드 _CANDIDATES_REFRESH_DISPATCHED_DETAIL 그대로). */
const REFRESH_ACCEPTED = {
  task_id: "task-preview-1",
  operator_id: 1,
  current_operator_id: 1,
  current_operator_username: "operator",
  high_priority_only: false,
  snapshot_status: "running" as const,
  detail: "미리보기 재계산을 큐에 등록했습니다.",
  poll_url: "/api/v1/operator/strategy/candidates"
};

/** 후보 GET 응답을 순서대로 돌려준다(마지막 값에서 고정) + 갱신 202. */
function installFetchMock(
  payloads: OperatorStrategyCandidatesResponse[],
  refresh: unknown = REFRESH_ACCEPTED
) {
  let index = 0;
  const fetchMock = vi.fn((input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/strategy/candidates/refresh")) return jsonResponse(refresh, 202);
    if (url.includes("/strategy/candidates")) {
      const payload = payloads[Math.min(index, payloads.length - 1)]!;
      index += 1;
      return jsonResponse(payload);
    }
    return jsonResponse({}, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function refreshCalls(fetchMock: ReturnType<typeof vi.fn>) {
  return fetchMock.mock.calls.filter(
    ([url, init]) =>
      String(url).includes("/strategy/candidates/refresh") &&
      (init as RequestInit | undefined)?.method === "POST"
  );
}

function candidatesCalls(fetchMock: ReturnType<typeof vi.fn>) {
  return fetchMock.mock.calls.filter(
    ([url]) => String(url).includes("/strategy/candidates") && !String(url).includes("/refresh")
  );
}

async function settle(ms: number = POLL_MS * 5) {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, ms));
  });
}

function renderPreview() {
  const queryClient = createTestQueryClient();
  const context = { session } as unknown as ShellOutletContext;
  const result = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/dashboard/strategy"]}>
        <Routes>
          <Route element={<Outlet context={context} />}>
            <Route
              path="/dashboard/strategy"
              element={<CandidatesPreview pollIntervalMs={POLL_MS} />}
            />
          </Route>
        </Routes>
        <Toaster />
      </MemoryRouter>
    </QueryClientProvider>
  );
  return { ...result, queryClient };
}

beforeEach(() => {
  vi.restoreAllMocks();
  act(() => {
    toastApi.clearAll();
  });
});

describe("CandidatesPreview 스냅샷 렌더", () => {
  it("저장된 스냅샷을 즉시 렌더하고 'N분 전 기준' 배지를 보여준다", async () => {
    const fetchMock = installFetchMock([snapshot()]);
    renderPreview();

    expect(await screen.findByText("3분 전 기준")).toBeInTheDocument();
    expect(screen.getByText(CANDIDATE_TITLE)).toBeInTheDocument();
    // evaluated_project_count 는 스냅샷의 고정 분석 예산(250)이고 요청 limit(5)과
    // 무관하다 — 라벨과 각주가 그렇게 말한다(소비자 주의 4).
    expect(screen.getByText("분석 대상")).toBeInTheDocument();
    expect(screen.getByText("250건")).toBeInTheDocument();
    expect(screen.getByText(/고정 분석 예산/)).toBeInTheDocument();
    // 정착 상태라 진행/실패 표시가 없고 폴링도 돌지 않는다.
    expect(screen.queryByTestId("snapshot-progress")).toBeNull();
    expect(screen.queryByTestId("snapshot-failed")).toBeNull();
    const settledCalls = candidatesCalls(fetchMock).length;
    await settle();
    expect(candidatesCalls(fetchMock).length).toBe(settledCalls);
  });

  it("stale=true 만으로 '갱신 중'을 말하지 않고 '갱신 필요'까지만 말한다", async () => {
    // 실패 쿨다운(60s) 중에는 stale 이면서 자동 디스패치가 억제된다(주의 1).
    installFetchMock([snapshot({ stale: true, computed_at: minutesAgo(40) })]);
    renderPreview();

    expect(await screen.findByText("40분 전 기준 · 갱신 필요")).toBeInTheDocument();
    expect(screen.queryByTestId("snapshot-progress")).toBeNull();
  });

  it("computed_at=null 이면 '첫 계산 대기' + 경과 안내를 보여주고 0건을 사실처럼 그리지 않는다", async () => {
    installFetchMock([bootstrapSnapshot]);
    renderPreview();

    expect(await screen.findByText("첫 계산 대기")).toBeInTheDocument();
    const progress = await screen.findByTestId("snapshot-progress");
    expect(progress).toHaveAttribute("role", "status");
    expect(progress).toHaveTextContent("다시 계산하고 있습니다");
    expect(progress).toHaveTextContent("초 경과");
    expect(progress).toHaveTextContent("최초 계산은 수십 초");
    // 부트스트랩 0건을 "매칭 후보 없음"으로 오도하지 않는다(§2 정직 명세).
    expect(screen.queryByText("현재 매칭되는 후보가 없습니다.")).toBeNull();
    expect(screen.queryByText("분석 대상")).toBeNull();
  });

  it("running → idle 로 전이하면 목록을 그리고 폴링을 멈춘다", async () => {
    const settled = snapshot();
    const fetchMock = installFetchMock([snapshot({ snapshot_status: "running" }), settled]);
    renderPreview();

    await screen.findByTestId("snapshot-progress");
    expect(await screen.findByText("3분 전 기준")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByTestId("snapshot-progress")).toBeNull());
    expect(screen.getByText(CANDIDATE_TITLE)).toBeInTheDocument();

    const settledCalls = candidatesCalls(fetchMock).length;
    await settle();
    expect(candidatesCalls(fetchMock).length).toBe(settledCalls);
  });

  it("snapshot_status=failed 는 경고를 띄우면서 직전 후보를 계속 보여준다", async () => {
    const fetchMock = installFetchMock([snapshot({ snapshot_status: "failed" })]);
    renderPreview();

    const alert = await screen.findByTestId("snapshot-failed");
    expect(alert).toHaveAttribute("role", "alert");
    expect(alert).toHaveTextContent("최근 갱신이 실패했습니다");
    expect(alert).toHaveTextContent("직전에 성공한 계산 결과");
    // 이전 성공분은 유효하다 — 후보와 신선도를 지우지 않는다(주의 2).
    expect(screen.getByText(CANDIDATE_TITLE)).toBeInTheDocument();
    expect(screen.getByText("3분 전 기준")).toBeInTheDocument();
    // failed 는 terminal — 쿨다운 동안 재조회해도 답이 같으므로 폴링하지 않는다.
    const settledCalls = candidatesCalls(fetchMock).length;
    await settle();
    expect(candidatesCalls(fetchMock).length).toBe(settledCalls);
  });

  it("우선순위 높음만 체크는 high_priority_only=true 로 재조회한다", async () => {
    const fetchMock = installFetchMock([snapshot()]);
    renderPreview();
    await screen.findByText("3분 전 기준");

    const user = (await import("@testing-library/user-event")).default.setup();
    await user.click(screen.getByRole("checkbox", { name: "우선순위 높음만" }));

    await waitFor(() =>
      expect(
        candidatesCalls(fetchMock).some(([url]) =>
          String(url).includes("high_priority_only=true")
        )
      ).toBe(true)
    );
  });
});

describe("CandidatesPreview 명시 갱신", () => {
  it("새로고침은 POST /candidates/refresh 를 보내고 그 뒤 폴링으로 결과를 반영한다", async () => {
    const fresh = snapshot({ computed_at: new Date().toISOString() });
    const fetchMock = installFetchMock([
      snapshot({ stale: true, computed_at: minutesAgo(40) }), // 최초: stale 이지만 정착
      snapshot({ snapshot_status: "running", computed_at: minutesAgo(40) }), // 202 직후
      fresh // 재계산 완료
    ]);
    renderPreview();
    await screen.findByText("40분 전 기준 · 갱신 필요");

    const user = (await import("@testing-library/user-event")).default.setup();
    await user.click(screen.getByRole("button", { name: "새로고침" }));

    await waitFor(() => expect(refreshCalls(fetchMock)).toHaveLength(1));
    const [url, init] = refreshCalls(fetchMock)[0]!;
    expect(String(url)).toBe(
      "/api/v1/operator/strategy/candidates/refresh?high_priority_only=false"
    );
    expect(init?.method).toBe("POST");
    expect((init?.headers as Record<string, string>).Authorization).toBe(
      "Bearer token-candidates"
    );
    // 202 detail 을 그대로 보여준다(디스패치/스킵 사유가 서버 문구로 구분된다).
    expect(await screen.findByText("미리보기 재계산을 큐에 등록했습니다.")).toBeInTheDocument();
    // 폴링은 로컬 플래그가 아니라 서버가 돌려준 running 이 켠다.
    await screen.findByTestId("snapshot-progress");
    expect(await screen.findByText("방금 기준")).toBeInTheDocument();
  });

  it("갱신 중에도 새로고침 버튼은 활성 — 고착 running 회수 경로를 막지 않는다", async () => {
    installFetchMock([snapshot({ snapshot_status: "running" })]);
    renderPreview();

    await screen.findByTestId("snapshot-progress");
    expect(screen.getByRole("button", { name: "새로고침" })).toBeEnabled();
  });
});
