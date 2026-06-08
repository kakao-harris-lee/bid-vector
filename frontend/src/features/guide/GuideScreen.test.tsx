import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import { GuideScreen } from "./GuideScreen";

const mockNavigate = vi.fn();

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return { ...actual, useNavigate: () => mockNavigate };
});

function renderWithProviders(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("GuideScreen", () => {
  it("renders the guide heading", () => {
    renderWithProviders(<GuideScreen />);
    expect(screen.getByRole("heading", { name: "입찰 워크플로우 안내" })).toBeInTheDocument();
  });

  it("defaults to the KONEPS process tab", () => {
    renderWithProviders(<GuideScreen />);
    const konepsTab = screen.getByRole("tab", { name: "나라장터 입찰 절차" });
    expect(konepsTab).toHaveAttribute("aria-selected", "true");

    // 탭 A 콘텐츠가 보여야 한다.
    expect(screen.getByText("입찰참가자격 등록")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "공사" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "용역" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "물품" })).toBeInTheDocument();
  });

  it("toggles to the app workflow tab and shows the eight steps", async () => {
    const user = userEvent.setup();
    renderWithProviders(<GuideScreen />);

    const appTab = screen.getByRole("tab", { name: "이 앱 사용 흐름" });
    await user.click(appTab);

    expect(appTab).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "나라장터 입찰 절차" })).toHaveAttribute(
      "aria-selected",
      "false",
    );

    const panel = screen.getByRole("tabpanel");
    const items = within(panel).getAllByRole("listitem");
    expect(items).toHaveLength(8);
    expect(screen.getByText("전략 설정")).toBeInTheDocument();
  });

  it("exposes tablist / tab / tabpanel roles", () => {
    renderWithProviders(<GuideScreen />);
    expect(screen.getByRole("tablist", { name: "가이드 탭" })).toBeInTheDocument();
    expect(screen.getAllByRole("tab")).toHaveLength(2);
    expect(screen.getByRole("tabpanel")).toBeInTheDocument();
  });

  it("navigates from KONEPS step deep-link buttons to real routes", async () => {
    const user = userEvent.setup();
    renderWithProviders(<GuideScreen />);

    await user.click(screen.getByRole("button", { name: /회사 정보 편집/ }));
    expect(mockNavigate).toHaveBeenCalledWith("/dashboard/profile");

    await user.click(screen.getByRole("button", { name: /입찰 후보/ }));
    expect(mockNavigate).toHaveBeenCalledWith("/dashboard/opportunities");
  });

  it("navigates from app workflow deep-link buttons to real routes", async () => {
    const user = userEvent.setup();
    renderWithProviders(<GuideScreen />);

    await user.click(screen.getByRole("tab", { name: "이 앱 사용 흐름" }));
    await user.click(screen.getAllByRole("button", { name: /전략 편집 열기/ })[0]);
    expect(mockNavigate).toHaveBeenCalledWith("/dashboard/strategy");
  });
});
