import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { KonepsProcessFlow } from "./KonepsProcessFlow";
import { KONEPS_PROCESS_STEPS } from "../guideContent";

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return { ...actual, useNavigate: () => vi.fn() };
});

function renderFlow() {
  return render(
    <MemoryRouter>
      <KonepsProcessFlow />
    </MemoryRouter>,
  );
}

describe("KonepsProcessFlow", () => {
  it("renders all six KONEPS process steps", () => {
    renderFlow();
    const list = screen.getByRole("list");
    // 단계 항목 + 단계 사이 커넥터(aria-hidden)로 listitem 수가 6보다 많을 수 있으니
    // 단계 제목으로 6개를 검증한다.
    KONEPS_PROCESS_STEPS.forEach((step) => {
      expect(within(list).getByText(step.title)).toBeInTheDocument();
    });
  });

  it("distinguishes ourHelp steps from external-only steps via badges", () => {
    renderFlow();
    const helpBadges = screen.getAllByText(/우리 도움/);
    const externalBadges = screen.getAllByText("나라장터에서 진행");

    const expectedHelp = KONEPS_PROCESS_STEPS.filter((s) => s.ourHelp).length;
    const expectedExternal = KONEPS_PROCESS_STEPS.filter((s) => !s.ourHelp).length;

    // 각 ourHelp 단계는 배지 + 본문에 "★ 우리 도움" 문구를 모두 포함하므로 배지 수 이상이다.
    expect(helpBadges.length).toBeGreaterThanOrEqual(expectedHelp);
    expect(externalBadges).toHaveLength(expectedExternal);
  });
});
