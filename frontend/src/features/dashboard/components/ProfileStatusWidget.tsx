import { useMemo } from "react";
import { ArrowRight, Building2, CheckCircle2, Pencil } from "lucide-react";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle
} from "@/shared/components/ui";
import type { OperatorAccountItem } from "@/shared/api";
import type { OperatorProfileResponse } from "@/shared/types/profile";
import { formatCurrencyCompact } from "@/shared/lib";

/**
 * Compact dashboard widget that surfaces the operator's 5-axis profile state at
 * the top of HomeScreen.
 *
 * Rendering branches:
 *
 *   (a) `operatorAccount === null`  → skeleton (loading or not authenticated yet).
 *   (b) 5-axis detail loaded        → render the full 5-axis grid + progress
 *       gauge for *any* operator (PR #74 made `/operator/profile?operator_id=`
 *       a cross-operator read for privileged callers). When `isOwnContext` is
 *       true the bottom row exposes the "edit" / "단계별 입력 시작" CTA. When
 *       impersonating another company the CTA is replaced by a read-only
 *       notice that points the user back to their own company to make edits
 *       (PUT is still self-only on the backend).
 *
 * Loading / error fallbacks remain in place — when the detail is still in
 * flight we show a skeleton; when the fetch errors out we degrade to the
 * metadata-only CTA so the operator can still navigate to the editor.
 */
export interface ProfileStatusWidgetProps {
  /** Currently selected operator (from `useActiveOperator().currentOperator`). */
  operatorAccount: OperatorAccountItem | null;
  /** 5-axis payload for the active operator (token owner *or* impersonated). */
  profile: OperatorProfileResponse | null;
  /**
   * `true` when the active operator is the token owner (no operator_id picked).
   * Drives whether the edit CTA is exposed or replaced with a read-only notice.
   */
  isOwnContext: boolean;
  /** Loading flag — when true and `profile` is absent, show the skeleton. */
  isProfileLoading?: boolean;
  /** CTA: profile_configured=false → start the guided 4-step wizard. */
  onWizardEnter: () => void;
  /** CTA: profile_configured=true → open the edit screen. */
  onEdit: () => void;
}

/**
 * The five matching axes the backend uses for project scoring. Order is the
 * order we render them in the stat grid.
 */
const AXIS_KEYS = [
  "license",
  "region",
  "annual_revenue",
  "total_awards",
  "construction_capacity"
] as const;

type AxisKey = (typeof AXIS_KEYS)[number];

interface AxisCell {
  key: AxisKey;
  label: string;
  /** Display string ("—" when missing). */
  value: string;
  /** True when the axis is considered populated. Drives the progress gauge. */
  filled: boolean;
  /** Optional secondary line (e.g. chip preview). */
  secondary?: string;
}

/** Threshold for "populated" — keep aligned with `OperatorProfile.profile_configured` heuristics. */
function isPopulated(value: number | string | string[] | null | undefined): boolean {
  if (value === null || value === undefined) return false;
  if (typeof value === "number") return Number.isFinite(value) && value > 0;
  if (typeof value === "string") return value.trim().length > 0;
  if (Array.isArray(value)) return value.length > 0;
  return false;
}

function previewChips(values: string[], take = 3): string {
  if (!values.length) return "";
  const head = values.slice(0, take).join(", ");
  return values.length > take ? `${head} 외 ${values.length - take}건` : head;
}

function buildAxisCells(profile: OperatorProfileResponse): AxisCell[] {
  return [
    {
      key: "license",
      label: "면허",
      value: profile.license_codes.length
        ? `${profile.license_codes.length}개`
        : "—",
      filled: profile.license_codes.length > 0,
      secondary: previewChips(profile.license_codes)
    },
    {
      key: "region",
      label: "지역",
      value: profile.region_codes.length
        ? `${profile.region_codes.length}개`
        : "—",
      filled: profile.region_codes.length > 0,
      secondary: previewChips(profile.region_codes)
    },
    {
      key: "annual_revenue",
      label: "연매출",
      value: profile.annual_revenue > 0
        ? formatCurrencyCompact(profile.annual_revenue)
        : "—",
      filled: profile.annual_revenue > 0
    },
    {
      key: "total_awards",
      label: "실적",
      value: profile.total_awards > 0
        ? `${profile.total_awards.toLocaleString("ko-KR")}건`
        : "—",
      filled: profile.total_awards > 0
    },
    {
      key: "construction_capacity",
      label: "시공능력",
      value: profile.construction_capacity_amount > 0
        ? formatCurrencyCompact(profile.construction_capacity_amount)
        : "—",
      filled: profile.construction_capacity_amount > 0
    }
  ];
}

export function ProfileStatusWidget({
  operatorAccount,
  profile,
  isOwnContext,
  isProfileLoading = false,
  onWizardEnter,
  onEdit
}: ProfileStatusWidgetProps) {
  // (a) operatorAccount not loaded yet (auth + accounts query still pending).
  if (!operatorAccount) {
    return (
      <Card
        aria-label="내 자격 상태"
        aria-busy="true"
        className="profile-status-widget"
      >
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-sm font-semibold">
            <Building2 size={16} aria-hidden="true" />
            <span>내 자격 상태</span>
          </CardTitle>
        </CardHeader>
        <CardContent className="pt-0">
          <div
            className="h-16 animate-pulse rounded-md bg-[var(--color-secondary)]"
            data-testid="profile-status-skeleton"
          />
        </CardContent>
      </Card>
    );
  }

  const companyLabel =
    operatorAccount.company?.trim() || operatorAccount.full_name?.trim() || operatorAccount.username;
  const businessType =
    operatorAccount.business_type?.trim() ||
    (profile?.business_type?.trim() ?? "");

  return (
    <Card
      aria-label="내 자격 상태"
      className="profile-status-widget"
    >
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-center gap-2">
          <CardTitle className="flex items-center gap-2 text-sm font-semibold">
            <Building2 size={16} aria-hidden="true" />
            <span>내 자격 상태</span>
          </CardTitle>
          <ConfiguredBadge
            configured={
              isOwnContext
                ? (profile?.profile_configured ?? operatorAccount.profile_configured)
                : operatorAccount.profile_configured
            }
          />
          {operatorAccount.is_synthetic ? (
            <Badge tone="muted" data-testid="synthetic-badge">
              가상 운영자
            </Badge>
          ) : null}
        </div>
        <div className="mt-1 flex flex-wrap items-baseline gap-2 text-xs text-[var(--color-muted)]">
          <strong className="text-sm font-semibold text-[var(--color-fg)]">
            {companyLabel}
          </strong>
          {businessType ? <span>· {businessType}</span> : null}
        </div>
      </CardHeader>
      <CardContent className="pt-0">
        <DetailBody
          profile={profile}
          isLoading={isProfileLoading}
          isOwnContext={isOwnContext}
          onWizardEnter={onWizardEnter}
          onEdit={onEdit}
          fallbackConfigured={operatorAccount.profile_configured}
        />
      </CardContent>
    </Card>
  );
}

function ConfiguredBadge({ configured }: { configured: boolean }) {
  if (configured) {
    return (
      <Badge tone="healthy" aria-label="프로필 입력 완료">
        <CheckCircle2 size={11} aria-hidden="true" />
        <span>입력 완료</span>
      </Badge>
    );
  }
  return (
    <Badge tone="watch" aria-label="프로필 입력 필요">
      입력 필요
    </Badge>
  );
}

function DetailBody({
  profile,
  isLoading,
  isOwnContext,
  onWizardEnter,
  onEdit,
  fallbackConfigured
}: {
  profile: OperatorProfileResponse | null;
  isLoading: boolean;
  isOwnContext: boolean;
  onWizardEnter: () => void;
  onEdit: () => void;
  fallbackConfigured: boolean;
}) {
  const cells = useMemo<AxisCell[] | null>(
    () => (profile ? buildAxisCells(profile) : null),
    [profile]
  );

  // Detail still in flight — show the loading skeleton in either context.
  if (!cells && isLoading) {
    return (
      <div
        className="h-20 animate-pulse rounded-md bg-[var(--color-secondary)]"
        data-testid="profile-status-detail-skeleton"
      />
    );
  }

  // Query errored / never resolved — degrade to the metadata-only CTA so the
  // widget still has a useful action surface. We rely on the metadata badge
  // already rendered in the header (via `fallbackConfigured`).
  if (!cells) {
    return (
      <div className="flex flex-col gap-3">
        <p className="text-xs text-[var(--color-muted)]">
          5축 상세를 불러오는 중에 문제가 발생했습니다. 잠시 후 새로고침 해 주세요.
        </p>
        <ActionFooter
          isOwnContext={isOwnContext}
          configured={fallbackConfigured}
          onEdit={onEdit}
          onWizardEnter={onWizardEnter}
        />
      </div>
    );
  }

  const populatedCount = cells.filter((cell) => cell.filled).length;
  const progressPct = profile?.profile_configured
    ? 100
    : Math.round((populatedCount / cells.length) * 100);
  const configured = profile?.profile_configured ?? fallbackConfigured;

  return (
    <div className="flex flex-col gap-3">
      <ul
        className="grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-5"
        aria-label="5축 자격 요약"
      >
        {cells.map((cell) => (
          <li
            key={cell.key}
            className="flex flex-col gap-0.5 rounded-md border border-[var(--color-border)] bg-[var(--color-background)] px-2 py-1.5"
          >
            <span className="text-[10px] uppercase tracking-wide text-[var(--color-muted)]">
              {cell.label}
            </span>
            <strong
              className={
                cell.filled
                  ? "text-sm font-semibold text-[var(--color-fg)]"
                  : "text-sm font-semibold text-[var(--color-muted)]"
              }
            >
              {cell.value}
            </strong>
            {cell.secondary ? (
              <span className="truncate text-[10px] text-[var(--color-muted)]">
                {cell.secondary}
              </span>
            ) : null}
          </li>
        ))}
      </ul>
      <div className="flex flex-col gap-1">
        <div className="flex items-center justify-between text-xs text-[var(--color-muted)]">
          <span>입력 진행률</span>
          <span aria-label={`입력 진행률 ${progressPct}%`}>{progressPct}%</span>
        </div>
        <div
          className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--color-secondary)]"
          role="progressbar"
          aria-valuenow={progressPct}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="입력 진행률 게이지"
        >
          <div
            className="h-full bg-[var(--color-primary)] transition-all"
            style={{ width: `${progressPct}%` }}
            data-testid="profile-progress-fill"
          />
        </div>
      </div>
      <ActionFooter
        isOwnContext={isOwnContext}
        configured={configured}
        onEdit={onEdit}
        onWizardEnter={onWizardEnter}
      />
    </div>
  );
}

/**
 * Bottom-row action bar. For the token owner it exposes the wizard / edit CTA;
 * for impersonation views it renders a read-only notice so the operator
 * understands edits must happen on their own company (PR #74 PUT 403 policy).
 */
function ActionFooter({
  isOwnContext,
  configured,
  onEdit,
  onWizardEnter
}: {
  isOwnContext: boolean;
  configured: boolean;
  onEdit: () => void;
  onWizardEnter: () => void;
}) {
  if (!isOwnContext) {
    return (
      <p
        className="text-right text-xs text-[var(--color-muted)]"
        data-testid="other-context-hint"
      >
        편집은 본인 회사로 돌아가야 가능합니다.
      </p>
    );
  }
  return (
    <div className="flex justify-end">
      {configured ? (
        <Button variant="outline" size="sm" onClick={onEdit}>
          <Pencil size={14} aria-hidden="true" />
          편집
        </Button>
      ) : (
        <Button size="sm" onClick={onWizardEnter}>
          단계별 입력 시작
          <ArrowRight size={14} aria-hidden="true" />
        </Button>
      )}
    </div>
  );
}
