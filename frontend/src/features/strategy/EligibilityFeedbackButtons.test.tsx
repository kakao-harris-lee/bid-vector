import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders } from "@/test-utils";
import { toastApi } from "@/shared/components/ui";
import type { EligibilityFeedbackResponse } from "@/shared/api";
import type { AuthSession } from "@/app/layout/AuthGate";
import { EligibilityFeedbackButtons } from "./EligibilityFeedbackButtons";

const session: AuthSession = { token: "token-feedback", username: "operator" };

const FEEDBACK_URL = "/api/v1/operator/eligibility-feedback";

function feedbackResponse(
  projectId: number,
  verdict: string
): EligibilityFeedbackResponse {
  return {
    project_id: projectId,
    verdict,
    label: verdict === "적합" ? "eligible" : verdict === "부적합" ? "ineligible" : "hold",
    source: "operator",
    rationale: null,
    labeler_version: "v1",
    operator_id: 1
  };
}

function jsonResponse(payload: unknown, status = 200): Promise<Response> {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(payload)
  } as Response);
}

function lastFeedbackCall(fetchMock: ReturnType<typeof vi.fn>) {
  const calls = fetchMock.mock.calls.filter(
    ([url, init]) =>
      String(url) === FEEDBACK_URL && (init as RequestInit | undefined)?.method === "POST"
  );
  return calls[calls.length - 1];
}

beforeEach(() => {
  vi.restoreAllMocks();
  act(() => {
    toastApi.clearAll();
  });
});

describe("EligibilityFeedbackButtons", () => {
  it("적합/부적합/보류 세 버튼을 렌더한다", () => {
    vi.stubGlobal("fetch", vi.fn());
    renderWithProviders(<EligibilityFeedbackButtons projectId={42} session={session} />);

    expect(screen.getByRole("button", { name: "적합 피드백" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "부적합 피드백" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "보류 피드백" })).toBeInTheDocument();
  });

  it("verdict 클릭 시 project_id·verdict·bearer 토큰으로 POST하고 선택을 하이라이트한다", async () => {
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) =>
      jsonResponse(feedbackResponse(42, JSON.parse(String(init?.body)).verdict))
    );
    vi.stubGlobal("fetch", fetchMock);

    renderWithProviders(<EligibilityFeedbackButtons projectId={42} session={session} />);

    await userEvent.click(screen.getByRole("button", { name: "적합 피드백" }));

    await waitFor(() => expect(lastFeedbackCall(fetchMock)).toBeDefined());
    const [url, init] = lastFeedbackCall(fetchMock)!;
    expect(String(url)).toBe(FEEDBACK_URL);
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ project_id: 42, verdict: "적합" });
    expect((init?.headers as Record<string, string>).Authorization).toBe(
      "Bearer token-feedback"
    );

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "적합 피드백" })).toHaveAttribute(
        "aria-pressed",
        "true"
      )
    );
    expect(screen.getByRole("button", { name: "부적합 피드백" })).toHaveAttribute(
      "aria-pressed",
      "false"
    );
    expect(await screen.findByText("'적합' 피드백을 저장했습니다")).toBeInTheDocument();
  });

  it("다른 verdict를 재클릭하면 새 POST를 보내고 하이라이트를 옮긴다", async () => {
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) =>
      jsonResponse(feedbackResponse(7, JSON.parse(String(init?.body)).verdict))
    );
    vi.stubGlobal("fetch", fetchMock);

    renderWithProviders(<EligibilityFeedbackButtons projectId={7} session={session} />);

    await userEvent.click(screen.getByRole("button", { name: "적합 피드백" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "적합 피드백" })).toHaveAttribute(
        "aria-pressed",
        "true"
      )
    );

    await userEvent.click(screen.getByRole("button", { name: "부적합 피드백" }));

    await waitFor(() => {
      const call = lastFeedbackCall(fetchMock)!;
      expect(JSON.parse(String(call[1]?.body))).toEqual({
        project_id: 7,
        verdict: "부적합"
      });
    });
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "부적합 피드백" })).toHaveAttribute(
        "aria-pressed",
        "true"
      )
    );
    expect(screen.getByRole("button", { name: "적합 피드백" })).toHaveAttribute(
      "aria-pressed",
      "false"
    );
  });

  it("서버 실패 시 danger toast를 띄우고 하이라이트하지 않는다", async () => {
    const fetchMock = vi.fn(() => jsonResponse({ detail: "boom" }, 500));
    vi.stubGlobal("fetch", fetchMock);

    renderWithProviders(<EligibilityFeedbackButtons projectId={9} session={session} />);

    await userEvent.click(screen.getByRole("button", { name: "보류 피드백" }));

    expect(await screen.findByText("식별 피드백 실패")).toBeInTheDocument();
    expect(screen.getByText("식별 피드백 저장에 실패했습니다.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "보류 피드백" })).toHaveAttribute(
      "aria-pressed",
      "false"
    );
  });
});
