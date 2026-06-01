import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "@/test-utils";
import { CustomOperatorManager } from "./CustomOperatorManager";
import type { SyntheticOperatorListResponse } from "@/shared/types/synthetic";

const operators: SyntheticOperatorListResponse = {
  operator_count: 2,
  operators: [
    {
      user_id: 1,
      username: "synthetic-aggressive",
      slug: "aggressive",
      display_name: "공격형",
      company: "공격 주식회사",
      business_type: "공사",
      annual_revenue: 1_000_000_000,
      capacity_score: 0.8,
      bid_now_threshold: 0.85,
      review_threshold: 0.6,
      is_custom: false
    },
    {
      user_id: 2,
      username: "synthetic-custom-우리회사",
      slug: "custom-우리회사",
      display_name: "우리회사",
      company: "우리 주식회사",
      business_type: "용역",
      annual_revenue: 500_000_000,
      capacity_score: 0.5,
      bid_now_threshold: 0.7,
      review_threshold: 0.5,
      is_custom: true
    }
  ]
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
  vi.restoreAllMocks();
});

describe("CustomOperatorManager", () => {
  it("separates preset and custom companies with protection labels", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => jsonResponse(operators))
    );

    renderWithProviders(<CustomOperatorManager token="token-x" />);

    expect(await screen.findByText("우리회사")).toBeInTheDocument();
    expect(screen.getByText("공격형")).toBeInTheDocument();
    expect(screen.getByText("커스텀")).toBeInTheDocument();
    expect(screen.getByText("프리셋")).toBeInTheDocument();
    // 보호 안내 문구가 보여야 한다.
    expect(
      screen.getByText(/프리셋은 편집·삭제할 수 없습니다/)
    ).toBeInTheDocument();
  });

  it("shows edit/clone/delete for custom and only clone for preset", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => jsonResponse(operators))
    );

    renderWithProviders(<CustomOperatorManager token="token-x" />);
    await screen.findByText("우리회사");

    // 커스텀 회사: 편집/복제/삭제 버튼 존재.
    expect(screen.getByRole("button", { name: "편집" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "삭제" })).toBeInTheDocument();
    // 복제 버튼은 프리셋(1) + 커스텀(1) = 2개.
    expect(screen.getAllByRole("button", { name: "복제" })).toHaveLength(2);
    // 프리셋에는 편집/삭제 버튼이 없다(커스텀 1개분만 존재).
    expect(screen.getAllByRole("button", { name: "편집" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "삭제" })).toHaveLength(1);
  });

  it("requires confirmation before deleting a custom company", async () => {
    const deleteSpy = vi.fn();
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (
          url.includes("/api/v1/synthetic/custom-operators/") &&
          init?.method === "DELETE"
        ) {
          deleteSpy(url);
          return jsonResponse({
            deleted: true,
            slug: "custom-우리회사",
            username: "synthetic-custom-우리회사"
          });
        }
        return jsonResponse(operators);
      })
    );

    renderWithProviders(<CustomOperatorManager token="token-x" />);
    await screen.findByText("우리회사");

    // 1) 삭제 클릭 → 확인 단계로 전환되며 아직 DELETE 안 됨.
    fireEvent.click(screen.getByRole("button", { name: "삭제" }));
    expect(await screen.findByRole("button", { name: "삭제 확인" })).toBeInTheDocument();
    expect(deleteSpy).not.toHaveBeenCalled();

    // 2) 삭제 확인 → DELETE 호출.
    fireEvent.click(screen.getByRole("button", { name: "삭제 확인" }));
    await waitFor(() => expect(deleteSpy).toHaveBeenCalledTimes(1));
    expect(deleteSpy.mock.calls[0][0]).toContain(
      "/api/v1/synthetic/custom-operators/"
    );
  });

  it("opens the create form from the new-company button", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => jsonResponse(operators))
    );

    renderWithProviders(<CustomOperatorManager token="token-x" />);
    await screen.findByText("우리회사");

    fireEvent.click(screen.getByRole("button", { name: "새 커스텀 회사" }));

    expect(screen.getByLabelText("커스텀 회사 생성")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /회사 생성/ })).toBeInTheDocument();
  });
});
