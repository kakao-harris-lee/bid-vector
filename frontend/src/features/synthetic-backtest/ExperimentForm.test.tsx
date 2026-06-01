import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "@/test-utils";
import { ExperimentForm } from "./ExperimentForm";
import type { SyntheticOperatorListResponse } from "@/shared/types/synthetic";

const operators: SyntheticOperatorListResponse = {
  operator_count: 2,
  operators: [
    {
      user_id: 11,
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
      user_id: 21,
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

describe("ExperimentForm", () => {
  it("renders the name field and operator presets", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => jsonResponse(operators))
    );

    renderWithProviders(<ExperimentForm token="token-x" />);

    expect(screen.getByLabelText("이름")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /실험 저장/ })).toBeInTheDocument();
    expect(await screen.findByText("공격형")).toBeInTheDocument();
  });

  it("labels operators with preset/custom badges", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => jsonResponse(operators))
    );

    renderWithProviders(<ExperimentForm token="token-x" />);

    expect(await screen.findByText("우리회사")).toBeInTheDocument();
    expect(screen.getByText("커스텀")).toBeInTheDocument();
    expect(screen.getByText("프리셋")).toBeInTheDocument();
  });

  it("blocks submit when the name is empty", async () => {
    const createSpy = vi.fn(() => jsonResponse({}));
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url === "/api/v1/synthetic/experiments" && init?.method === "POST") {
          return createSpy();
        }
        return jsonResponse(operators);
      })
    );

    renderWithProviders(<ExperimentForm token="token-x" />);

    fireEvent.click(screen.getByRole("button", { name: /실험 저장/ }));

    expect(await screen.findByText("실험 이름을 입력하세요.")).toBeInTheDocument();
    expect(createSpy).not.toHaveBeenCalled();
  });

  it("submits a valid experiment and notifies the parent", async () => {
    const created = {
      id: 7,
      name: "테스트 실험",
      description: null,
      params: { limit: 100, scenario: "base", settle_actions: false },
      operator_slugs: [],
      runs: []
    };
    let postBody: unknown = null;
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url === "/api/v1/synthetic/experiments" && init?.method === "POST") {
          postBody = init?.body ? JSON.parse(String(init.body)) : null;
          return jsonResponse(created, 201);
        }
        return jsonResponse(operators);
      })
    );
    const onCreated = vi.fn();

    renderWithProviders(<ExperimentForm token="token-x" onCreated={onCreated} />);

    fireEvent.change(screen.getByLabelText("이름"), { target: { value: "테스트 실험" } });
    fireEvent.click(screen.getByRole("button", { name: /실험 저장/ }));

    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(created));
    expect((postBody as { name: string }).name).toBe("테스트 실험");
    expect((postBody as { params: { scenario: string } }).params.scenario).toBe("base");
  });
});
