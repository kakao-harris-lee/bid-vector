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
/**
 * 정착-idle recheck 를 settle 창(≈100ms)보다 훨씬 크게 잡아, count 가드에서 슬로우
 * 패스가 이 창에 추가 조회를 내지 않음을 못박는다 — 그래도 정착-idle 을 fast 로
 * 되돌리면 가드가 깨진다(Finding 1a teeth).
 */
const IDLE_RECHECK_LARGE_MS = 100_000;
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

/**
 * 후보 GET 이 항상 `current` 를 돌려주도록 래치한다 — running 같은 과도 상태가 한
 * 폴링 창에만 존재해 findBy 가 놓치는 구조적 flake 를 없앤다(설계 리뷰 Finding 2).
 * 테스트가 `advance(next)` 로 다음 상태를 밀어 넣을 때만 응답이 바뀐다.
 */
function installGatedFetchMock(
  initial: OperatorStrategyCandidatesResponse,
  refresh: unknown = REFRESH_ACCEPTED
) {
  let current = initial;
  const fetchMock = vi.fn((input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/strategy/candidates/refresh")) return jsonResponse(refresh, 202);
    if (url.includes("/strategy/candidates")) return jsonResponse(current);
    return jsonResponse({}, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return {
    fetchMock,
    advance: (next: OperatorStrategyCandidatesResponse) => {
      current = next;
    }
  };
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

function renderPreview(
  opts: { pollIntervalMs?: number; idleRecheckMs?: number } = {}
) {
  const pollIntervalMs = opts.pollIntervalMs ?? POLL_MS;
  // 기본은 큰 recheck — 정착-idle 이 이 창에서 스스로 재조회하지 않게 해 count
  // 가드를 결정적으로 만든다. 슬로우 패스를 검증하는 테스트만 작은 값을 주입한다.
  const idleRecheckMs = opts.idleRecheckMs ?? IDLE_RECHECK_LARGE_MS;
  const queryClient = createTestQueryClient();
  const context = { session } as unknown as ShellOutletContext;
  const result = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/dashboard/strategy"]}>
        <Routes>
          <Route element={<Outlet context={context} />}>
            <Route
              path="/dashboard/strategy"
              element={
                <CandidatesPreview
                  pollIntervalMs={pollIntervalMs}
                  idleRecheckMs={idleRecheckMs}
                />
              }
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
  it("첫 응답 전에는 '불러오는 중…' 로딩 표시로 빈 카드가 아니게 한다(Finding 3)", async () => {
    // 응답을 붙잡아 pending 창을 관찰한다(ExperimentRunProgress 로딩 라벨 패턴).
    let resolveFetch!: (response: Response) => void;
    const pending = new Promise<Response>((resolve) => {
      resolveFetch = resolve;
    });
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/strategy/candidates")) return pending;
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderPreview();

    expect(await screen.findByText("불러오는 중…")).toBeInTheDocument();

    await act(async () => {
      resolveFetch({
        ok: true,
        status: 200,
        json: () => Promise.resolve(snapshot())
      } as Response);
    });

    expect(await screen.findByText("3분 전 기준")).toBeInTheDocument();
    // 응답이 오면 로딩 표시는 사라진다.
    expect(screen.queryByText("불러오는 중…")).toBeNull();
  });

  it("저장된 스냅샷을 즉시 렌더하고 'N분 전 기준' 배지를 보여준다", async () => {
    const fetchMock = installFetchMock([snapshot()]);
    // 느슨한 recheck 를 크게 주입 — 정착-idle 이 이 창에서 추가 조회를 내지 않음을
    // 못박는다. 정착-idle 을 fast 로 되돌리면 이 count 가드가 깨진다(Finding 1a teeth).
    renderPreview({ idleRecheckMs: IDLE_RECHECK_LARGE_MS });

    expect(await screen.findByText("3분 전 기준")).toBeInTheDocument();
    expect(screen.getByText(CANDIDATE_TITLE)).toBeInTheDocument();
    // evaluated_project_count 는 스냅샷의 고정 분석 예산(250)이고 요청 limit(5)과
    // 무관하다 — 라벨과 각주가 그렇게 말한다(소비자 주의 4).
    expect(screen.getByText("분석 대상")).toBeInTheDocument();
    expect(screen.getByText("250건")).toBeInTheDocument();
    expect(screen.getByText(/고정 분석 예산/)).toBeInTheDocument();
    // 정착-idle 이라 진행/실패 표시가 없고 느슨한 recheck 창 밖이라 폴링도 없다.
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
    // running 을 래치해 과도 창을 놓치지 않는다(Finding 2 — 시퀀스 mock 의 한-폴링-창
    // 경합 제거). advance 로 정착시킨다.
    const { fetchMock, advance } = installGatedFetchMock(
      snapshot({ snapshot_status: "running" })
    );
    renderPreview({ pollIntervalMs: POLL_MS, idleRecheckMs: IDLE_RECHECK_LARGE_MS });

    await screen.findByTestId("snapshot-progress");
    advance(snapshot());
    expect(await screen.findByText("3분 전 기준")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByTestId("snapshot-progress")).toBeNull());
    expect(screen.getByText(CANDIDATE_TITLE)).toBeInTheDocument();

    // 정착-idle 은 느슨한 recheck(크게 주입) 라 이 창에서는 추가 조회가 없다.
    // 정착-idle 을 fast 로 되돌리면 이 count 가드가 깨진다(Finding 1a teeth).
    const settledCalls = candidatesCalls(fetchMock).length;
    await settle();
    expect(candidatesCalls(fetchMock).length).toBe(settledCalls);
  });

  it("정착-idle 은 (fast 가 아니라) 느슨한 recheck 주기로 서버 상태를 다시 확인한다", async () => {
    // fast 폴링은 크게(억제), 느슨한 recheck 만 작게 주입해 '정착-idle 이 fast 가
    // 아니라 recheck 주기로 다시 물음'을 못박는다. recheck 응답이 failed(terminal)면
    // recheck 는 정확히 한 번으로 끝난다. 슬로우 패스를 false 로 되돌리면 recheck 가
    // 아예 안 일어나고, fast 로 되돌리면 pollIntervalMs(크게) 라 창 안에 안 일어나
    // 어느 쪽이든 이 가드가 깨진다(Finding 1a teeth — false/fast 회귀 동시 방어).
    const fetchMock = installFetchMock([snapshot(), snapshot({ snapshot_status: "failed" })]);
    renderPreview({ pollIntervalMs: IDLE_RECHECK_LARGE_MS, idleRecheckMs: POLL_MS });
    await screen.findByText("3분 전 기준");
    const before = candidatesCalls(fetchMock).length;

    await waitFor(() => expect(candidatesCalls(fetchMock).length).toBe(before + 1));
    await screen.findByTestId("snapshot-failed");

    // failed 로 정착한 뒤에는 recheck 도 멈춘다(terminal).
    const after = candidatesCalls(fetchMock).length;
    await settle();
    expect(candidatesCalls(fetchMock).length).toBe(after);
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
    // 상태를 래치하고 advance 로 밀어 running 창을 안정적으로 관찰한다(Finding 2 —
    // POST/토스트 await 뒤 한-폴링-창 running 을 놓치던 flake 제거).
    const { fetchMock, advance } = installGatedFetchMock(
      snapshot({ stale: true, computed_at: minutesAgo(40) })
    );
    renderPreview({ pollIntervalMs: POLL_MS, idleRecheckMs: POLL_MS });
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

    // 폴링은 로컬 플래그가 아니라 서버가 돌려준 running 이 켠다 — running 이 래치돼
    // 진행 표시가 안정적으로 뜬다.
    advance(snapshot({ snapshot_status: "running", computed_at: minutesAgo(40) }));
    await screen.findByTestId("snapshot-progress");
    // 재계산 완료를 밀어 넣으면 다음 폴링이 방금 계산분을 반영한다.
    advance(snapshot({ computed_at: new Date().toISOString() }));
    expect(await screen.findByText("방금 기준")).toBeInTheDocument();
  });

  it("갱신 중에도 새로고침 버튼은 활성 — 고착 running 회수 경로를 막지 않는다", async () => {
    installFetchMock([snapshot({ snapshot_status: "running" })]);
    renderPreview();

    await screen.findByTestId("snapshot-progress");
    expect(screen.getByRole("button", { name: "새로고침" })).toBeEnabled();
  });
});

describe("CandidatesPreview 폴링 결정성", () => {
  it("['strategy'] 전면 invalidate 는 1회 재조회만 하고 폴링을 만들지 않는다", async () => {
    // 전략 저장 / realtime strategy.monitor.* / 온보딩 apply 가 모두 이 전면
    // invalidate 를 쏜다(설계 §7). 폴링 근거가 서버 status 뿐이므로, 정착 상태에서
    // 리셋되어도 폴링이 켜지지 않는다.
    const fetchMock = installFetchMock([snapshot()]);
    // 느슨한 recheck 를 크게 주입 — invalidate 1회 외에 정착-idle recheck 가 끼어들면
    // before+1 이 깨지므로, 슬로우 패스를 fast 로 되돌리면 이 가드가 잡는다.
    const { queryClient } = renderPreview({ idleRecheckMs: IDLE_RECHECK_LARGE_MS });
    await screen.findByText("3분 전 기준");
    const before = candidatesCalls(fetchMock).length;

    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ["strategy"] });
    });

    await waitFor(() => expect(candidatesCalls(fetchMock).length).toBe(before + 1));
    await settle();
    expect(candidatesCalls(fetchMock).length).toBe(before + 1);
    expect(screen.queryByTestId("snapshot-progress")).toBeNull();
  });

  it("invalidate 뒤 서버가 running 을 보고하면 그때 폴링이 켜진다", async () => {
    // 정착에서 시작 — running 은 advance 로 밀고 전면 invalidate 가 그 상태를 끌어온다.
    // running 픽업은 recheck 가 아니라 invalidate 가 하므로 idleRecheck 는 크게 둔다
    // (마지막 정착 count 가드가 결정적이도록).
    const { fetchMock, advance } = installGatedFetchMock(snapshot());
    const { queryClient } = renderPreview({
      pollIntervalMs: POLL_MS,
      idleRecheckMs: IDLE_RECHECK_LARGE_MS
    });
    await screen.findByText("3분 전 기준");

    advance(snapshot({ snapshot_status: "running" }));
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ["strategy"] });
    });

    await screen.findByTestId("snapshot-progress");
    advance(snapshot({ computed_at: new Date().toISOString() }));
    expect(await screen.findByText("방금 기준")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByTestId("snapshot-progress")).toBeNull());
    const settledCalls = candidatesCalls(fetchMock).length;
    await settle();
    expect(candidatesCalls(fetchMock).length).toBe(settledCalls);
  });
});
