import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
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

  it("renders all eight workflow steps", () => {
    renderWithProviders(<GuideScreen />);
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(8);
  });

  it("renders deep-link buttons that navigate to real routes", () => {
    renderWithProviders(<GuideScreen />);
    const strategyButton = screen.getAllByRole("button", { name: /전략 편집 열기/ })[0];
    expect(strategyButton).toBeInTheDocument();
  });
});
