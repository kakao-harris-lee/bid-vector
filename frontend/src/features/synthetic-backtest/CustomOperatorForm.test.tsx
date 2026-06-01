import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "@/test-utils";
import { CustomOperatorForm, listToText, textToList } from "./CustomOperatorForm";
import type { CustomOperatorDetail } from "@/shared/types/synthetic";

const detail: CustomOperatorDetail = {
  user_id: 21,
  username: "synthetic-custom-우리회사",
  slug: "custom-우리회사",
  is_custom: true,
  display_name: "우리회사",
  company: "우리 주식회사",
  business_type: "용역",
  annual_revenue: 500_000_000,
  capacity_score: 0.5,
  license_codes: ["0001", "0002"],
  region_codes: ["11"],
  focus_categories: ["용역"],
  focus_regions: ["서울"],
  exclude_regions: [],
  required_keywords: ["청소"],
  exclude_keywords: [],
  min_budget_estimate: 0,
  max_budget_estimate: 1_000_000_000,
  minimum_match_score: 0.3,
  minimum_probability_score: 0.4,
  bid_now_threshold: 0.7,
  review_threshold: 0.5,
  max_recommended_candidates: 5
};

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("textToList / listToText", () => {
  it("splits comma and newline separated text into a trimmed array", () => {
    expect(textToList("a, b ,\nc")).toEqual(["a", "b", "c"]);
    expect(textToList("")).toEqual([]);
    expect(textToList(undefined)).toEqual([]);
  });

  it("joins arrays into comma-separated text", () => {
    expect(listToText(["a", "b"])).toBe("a, b");
    expect(listToText([])).toBe("");
  });
});

describe("CustomOperatorForm", () => {
  it("renders the create form with a required name field", () => {
    const onSubmit = vi.fn();
    renderWithProviders(<CustomOperatorForm mode="create" onSubmit={onSubmit} />);

    expect(screen.getByLabelText("이름")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /회사 생성/ })).toBeInTheDocument();
  });

  it("blocks submit and shows an error when the name is empty", async () => {
    const onSubmit = vi.fn();
    renderWithProviders(<CustomOperatorForm mode="create" onSubmit={onSubmit} />);

    fireEvent.click(screen.getByRole("button", { name: /회사 생성/ }));

    expect(await screen.findByText("회사 이름을 입력하세요.")).toBeInTheDocument();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("submits a create payload with list fields converted to arrays", async () => {
    const onSubmit = vi.fn();
    renderWithProviders(<CustomOperatorForm mode="create" onSubmit={onSubmit} />);

    fireEvent.change(screen.getByLabelText("이름"), { target: { value: "신규 회사" } });
    fireEvent.change(screen.getByLabelText("관심 카테고리"), {
      target: { value: "용역, 공사" }
    });
    fireEvent.click(screen.getByRole("button", { name: /회사 생성/ }));

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    const payload = onSubmit.mock.calls[0][0];
    expect(payload.name).toBe("신규 회사");
    expect(payload.focus_categories).toEqual(["용역", "공사"]);
  });

  it("rejects a probability threshold outside the 0~1 range", async () => {
    const onSubmit = vi.fn();
    renderWithProviders(<CustomOperatorForm mode="create" onSubmit={onSubmit} />);

    fireEvent.change(screen.getByLabelText("이름"), { target: { value: "범위 테스트" } });
    fireEvent.change(screen.getByLabelText("bid_now_threshold"), {
      target: { value: "1.5" }
    });
    fireEvent.click(screen.getByRole("button", { name: /회사 생성/ }));

    expect(await screen.findByText("0~1")).toBeInTheDocument();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("prefills the edit form from detail and omits untouched empty lists", async () => {
    const onSubmit = vi.fn();
    renderWithProviders(
      <CustomOperatorForm mode="edit" initial={detail} onSubmit={onSubmit} />
    );

    expect((screen.getByLabelText("이름") as HTMLInputElement).value).toBe("우리회사");
    expect((screen.getByLabelText("관심 카테고리") as HTMLInputElement).value).toBe("용역");

    fireEvent.click(screen.getByRole("button", { name: /변경 저장/ }));

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    const payload = onSubmit.mock.calls[0][0];
    expect(payload.name).toBe("우리회사");
    expect(payload.focus_categories).toEqual(["용역"]);
    // 비어 있던 리스트는 부분 갱신에서 omit 되어야 기존 서버 값을 보존한다.
    expect(payload.exclude_regions).toBeUndefined();
  });
});
