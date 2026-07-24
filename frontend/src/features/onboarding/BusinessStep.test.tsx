import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { BusinessVerificationResponse } from "@/shared/api";
import { BusinessStep, type BusinessStepProps } from "./BusinessStep";

const session = { token: "token-business", username: "operator" };

function verificationResponse(
  status: string,
  overrides: Partial<BusinessVerificationResponse> = {}
): BusinessVerificationResponse {
  return {
    status,
    masked_number: "111-22-*****",
    verified_at: status === "unknown" ? null : "2026-07-24T01:23:00Z",
    detail: null,
    current_operator_id: 1,
    current_operator_username: "operator",
    ...overrides
  };
}

function jsonResponse(payload: unknown, status = 200): Promise<Response> {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(payload)
  } as Response);
}

function installFetchMock(payload: BusinessVerificationResponse, httpStatus = 200) {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/operator/business-verification")) {
      return jsonResponse(payload, httpStatus);
    }
    return jsonResponse({}, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function verifyCalls(fetchMock: ReturnType<typeof vi.fn>) {
  return fetchMock.mock.calls.filter(
    ([url, init]) =>
      String(url).includes("/operator/business-verification") &&
      (init as RequestInit | undefined)?.method === "POST"
  );
}

function renderStep(props: Partial<BusinessStepProps> = {}) {
  const onProceed = props.onProceed ?? vi.fn();
  const onSkip = props.onSkip ?? vi.fn();
  const queryClient = new QueryClient({
    defaultOptions: { mutations: { retry: false } }
  });
  render(
    <QueryClientProvider client={queryClient}>
      <BusinessStep session={session} onProceed={onProceed} onSkip={onSkip} />
    </QueryClientProvider>
  );
  return { onProceed, onSkip };
}

async function verify(user: ReturnType<typeof userEvent.setup>, number: string) {
  await user.type(screen.getByLabelText("사업자등록번호 (필수)"), number);
  await user.click(screen.getByRole("button", { name: /상태 확인/ }));
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("BusinessStep", () => {
  it("상태 확인 제출 시 입력 번호(숫자만)를 business-verification 엔드포인트로 전송한다", async () => {
    const fetchMock = installFetchMock(verificationResponse("verified"));
    renderStep();
    const user = userEvent.setup();
    // 하이픈 입력이라도 숫자 10자리로 정규화되어 전송된다(status-only: 개업일자/대표자명 없음).
    await verify(user, "111-22-23333");

    await waitFor(() => expect(verifyCalls(fetchMock)).toHaveLength(1));
    const [, init] = verifyCalls(fetchMock)[0]!;
    expect(JSON.parse(String(init?.body))).toEqual({
      business_number: "1112223333",
      start_date: null,
      representative_name: null
    });
    expect((init?.headers as Record<string, string>).Authorization).toBe("Bearer token-business");
  });

  it("개업일자+대표자명이 모두 있으면 진위확인 값으로 전송한다(둘 다 있어야 validate)", async () => {
    const fetchMock = installFetchMock(verificationResponse("verified"));
    renderStep();
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("사업자등록번호 (필수)"), "1112223333");
    await user.type(screen.getByLabelText("개업일자 (선택)"), "2020-01-02");
    await user.type(screen.getByLabelText("대표자명 (선택)"), "홍길동");
    await user.click(screen.getByRole("button", { name: /상태 확인/ }));

    await waitFor(() => expect(verifyCalls(fetchMock)).toHaveLength(1));
    const [, init] = verifyCalls(fetchMock)[0]!;
    expect(JSON.parse(String(init?.body))).toEqual({
      business_number: "1112223333",
      start_date: "20200102",
      representative_name: "홍길동"
    });
  });

  it("verified 응답은 masked 번호와 성공 표시를 노출하고, 입력한 원문을 확정값으로 재노출하지 않는다", async () => {
    installFetchMock(verificationResponse("verified"));
    renderStep();
    const user = userEvent.setup();
    await verify(user, "111-22-23333");

    const region = await screen.findByRole("status");
    // 응답의 masked 번호만 표시(원문 재노출 금지, §2)
    expect(within(region).getByText("111-22-*****")).toBeInTheDocument();
    expect(within(region).getByText("정상 확인")).toBeInTheDocument();
    expect(screen.getByText(/정상으로 확인되었습니다/)).toBeInTheDocument();
    // 입력한 원문(하이픈/숫자)은 확정값으로 노출되지 않는다
    expect(screen.queryByText("111-22-23333")).not.toBeInTheDocument();
    expect(screen.queryByText("1112223333")).not.toBeInTheDocument();
  });

  it.each(["inactive", "mismatch"])(
    "%s 응답은 진행 전 경고(warning gate)를 노출한다",
    async (status) => {
      installFetchMock(verificationResponse(status));
      renderStep();
      const user = userEvent.setup();
      await verify(user, "1112223333");

      await screen.findByRole("status");
      const alert = screen.getByRole("alert");
      expect(alert).toHaveTextContent("추천 진행 전 확인이 필요");
    }
  );

  it("unknown 응답은 비차단 정직 고지(검증 미구성)를 노출하고 진행을 막지 않는다", async () => {
    installFetchMock(verificationResponse("unknown", { detail: "검증 미구성" }));
    const { onProceed } = renderStep();
    const user = userEvent.setup();
    await verify(user, "1112223333");

    const region = await screen.findByRole("status");
    expect(within(region).getByText("확인 불가")).toBeInTheDocument();
    expect(screen.getByText(/검증 미구성 또는 확인 불가 상태/)).toBeInTheDocument();
    // 비차단 — warning alert 은 없다
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    // 그래도 다음 단계로 진행 가능
    await user.click(screen.getByRole("button", { name: /계속/ }));
    expect(onProceed).toHaveBeenCalledTimes(1);
  });

  it("건너뛰기는 사업자번호 없이 다음(seed) 단계로 넘긴다", async () => {
    installFetchMock(verificationResponse("unverified"));
    const { onSkip } = renderStep();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /건너뛰기/ }));
    expect(onSkip).toHaveBeenCalledTimes(1);
  });

  it("10자리가 아니면 검증 오류를 보이고 엔드포인트를 호출하지 않는다", async () => {
    const fetchMock = installFetchMock(verificationResponse("verified"));
    renderStep();
    const user = userEvent.setup();
    await verify(user, "12345");

    expect(await screen.findByText("사업자번호는 숫자 10자리입니다.")).toBeInTheDocument();
    expect(verifyCalls(fetchMock)).toHaveLength(0);
  });
});
