import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen, within } from "@testing-library/react";
import { renderWithProviders } from "@/test-utils";
import { ProfileStatusWidget } from "./ProfileStatusWidget";
import type { OperatorAccountItem } from "@/shared/api";
import type { OperatorProfileResponse } from "@/shared/types/profile";

const canonicalAccount: OperatorAccountItem = {
  operator_id: 1,
  username: "operator",
  full_name: "본사 운영자",
  company: "본사",
  business_type: "정보통신",
  is_canonical: true,
  is_synthetic: false,
  is_active: true,
  profile_configured: true
};

const syntheticAccount: OperatorAccountItem = {
  operator_id: 11,
  username: "synthetic-aggressive",
  full_name: "공격형",
  company: "Synthetic A",
  business_type: "정보통신",
  is_canonical: false,
  is_synthetic: true,
  is_active: true,
  profile_configured: true
};

const unconfiguredAccount: OperatorAccountItem = {
  ...canonicalAccount,
  profile_configured: false
};

const fullProfile: OperatorProfileResponse = {
  operator_id: 1,
  username: "operator",
  email: "operator@example.com",
  full_name: "본사 운영자",
  company: "본사",
  is_active: true,
  created_at: "2026-01-01T00:00:00Z",
  business_type: "정보통신",
  license_codes: ["정보통신공사업", "전기공사업", "소프트웨어사업자", "엔지니어링"],
  region_codes: ["서울", "경기", "부산"],
  annual_revenue: 5_000_000_000,
  capacity_score: 0.7,
  construction_capacity_amount: 12_000_000_000,
  awarded_contract_limit: 30_000_000_000,
  total_awards: 27,
  profile_configured: true
};

const emptyProfile: OperatorProfileResponse = {
  ...fullProfile,
  license_codes: [],
  region_codes: [],
  annual_revenue: 0,
  capacity_score: 0,
  construction_capacity_amount: 0,
  awarded_contract_limit: 0,
  total_awards: 0,
  profile_configured: false
};

describe("ProfileStatusWidget", () => {
  it("shows skeleton when operatorAccount is null (loading)", () => {
    renderWithProviders(
      <ProfileStatusWidget
        operatorAccount={null}
        profile={null}
        isOwnContext
        onWizardEnter={vi.fn()}
        onEdit={vi.fn()}
      />
    );

    const card = screen.getByLabelText("내 자격 상태");
    expect(card).toHaveAttribute("aria-busy", "true");
    expect(screen.getByTestId("profile-status-skeleton")).toBeInTheDocument();
  });

  it("renders the 5-axis grid and an 편집 button when own context + configured", () => {
    const onEdit = vi.fn();
    const onWizardEnter = vi.fn();
    renderWithProviders(
      <ProfileStatusWidget
        operatorAccount={canonicalAccount}
        profile={fullProfile}
        isOwnContext
        onWizardEnter={onWizardEnter}
        onEdit={onEdit}
      />
    );

    const grid = screen.getByLabelText("5축 자격 요약");
    expect(within(grid).getByText("면허")).toBeInTheDocument();
    expect(within(grid).getByText("4개")).toBeInTheDocument();
    expect(within(grid).getByText("지역")).toBeInTheDocument();
    expect(within(grid).getByText("3개")).toBeInTheDocument();
    expect(within(grid).getByText("실적")).toBeInTheDocument();
    expect(within(grid).getByText("27건")).toBeInTheDocument();

    // configured → 입력 완료 badge + 편집 button (no wizard CTA).
    expect(screen.getByLabelText("프로필 입력 완료")).toBeInTheDocument();
    const editButton = screen.getByRole("button", { name: /편집/ });
    fireEvent.click(editButton);
    expect(onEdit).toHaveBeenCalledTimes(1);
    expect(onWizardEnter).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: /단계별 입력 시작/ })).not.toBeInTheDocument();

    // progress gauge at 100%.
    const gauge = screen.getByRole("progressbar", { name: "입력 진행률 게이지" });
    expect(gauge).toHaveAttribute("aria-valuenow", "100");
  });

  it("shows wizard CTA + partial progress when own context + profile_configured=false", () => {
    const onWizardEnter = vi.fn();
    renderWithProviders(
      <ProfileStatusWidget
        operatorAccount={unconfiguredAccount}
        profile={emptyProfile}
        isOwnContext
        onWizardEnter={onWizardEnter}
        onEdit={vi.fn()}
      />
    );

    expect(screen.getByLabelText("프로필 입력 필요")).toBeInTheDocument();
    const cta = screen.getByRole("button", { name: /단계별 입력 시작/ });
    fireEvent.click(cta);
    expect(onWizardEnter).toHaveBeenCalledTimes(1);

    // Empty profile → 0% gauge.
    const gauge = screen.getByRole("progressbar", { name: "입력 진행률 게이지" });
    expect(gauge).toHaveAttribute("aria-valuenow", "0");

    // 5-axis cells show "—".
    const grid = screen.getByLabelText("5축 자격 요약");
    const dashes = within(grid).getAllByText("—");
    expect(dashes.length).toBeGreaterThanOrEqual(5);
  });

  it("falls back to metadata-only (no 5-axis grid) when not own context", () => {
    renderWithProviders(
      <ProfileStatusWidget
        operatorAccount={syntheticAccount}
        profile={null}
        isOwnContext={false}
        onWizardEnter={vi.fn()}
        onEdit={vi.fn()}
      />
    );

    expect(screen.getByText("Synthetic A")).toBeInTheDocument();
    expect(screen.getByTestId("synthetic-badge")).toBeInTheDocument();
    expect(screen.getByLabelText("프로필 입력 완료")).toBeInTheDocument();
    expect(screen.getByTestId("other-context-hint")).toHaveTextContent(
      "5축 상세는 본인 회사만 표시됩니다"
    );

    // No 5-axis grid and no action buttons for impersonation contexts.
    expect(screen.queryByLabelText("5축 자격 요약")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /편집/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /단계별 입력 시작/ })).not.toBeInTheDocument();
  });

  it("shows detail-skeleton when own context is still loading the 5-axis payload", () => {
    renderWithProviders(
      <ProfileStatusWidget
        operatorAccount={canonicalAccount}
        profile={null}
        isOwnContext
        isProfileLoading
        onWizardEnter={vi.fn()}
        onEdit={vi.fn()}
      />
    );

    expect(screen.getByTestId("profile-status-detail-skeleton")).toBeInTheDocument();
    expect(screen.queryByLabelText("5축 자격 요약")).not.toBeInTheDocument();
  });

  it("renders the metadata-only fallback CTA when profile fetch fails (own context, no data)", () => {
    const onWizardEnter = vi.fn();
    renderWithProviders(
      <ProfileStatusWidget
        operatorAccount={unconfiguredAccount}
        profile={null}
        isOwnContext
        isProfileLoading={false}
        onWizardEnter={onWizardEnter}
        onEdit={vi.fn()}
      />
    );

    // Header still shows the unconfigured-state badge from operatorAccount.
    expect(screen.getByLabelText("프로필 입력 필요")).toBeInTheDocument();
    // CTA matches the operatorAccount.profile_configured fallback (false → wizard).
    fireEvent.click(screen.getByRole("button", { name: /단계별 입력 시작/ }));
    expect(onWizardEnter).toHaveBeenCalledTimes(1);
    // Soft error copy so the user understands why the grid is missing.
    expect(
      screen.getByText(/5축 상세를 불러오는 중에 문제가 발생했습니다/)
    ).toBeInTheDocument();
  });
});
