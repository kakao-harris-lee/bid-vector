import { Badge } from "@/shared/components/ui";
import { useActiveOperator } from "@/app/operatorContext";
import { PROFILE_ROUTE_PATH, STRATEGY_ROUTE_PATH } from "../shellRoutes";

/**
 * Header-adjacent context bar that shows the operator the dashboard is
 * currently scoped to + a banner reminding the user that profile / strategy
 * editors stay locked to the token owner. Both pieces are read from the
 * active-operator context and rendered above every screen so the operator
 * never loses track of "whose data am I looking at?".
 */
export function ActiveOperatorContextBar({
  activeOperator,
  summaryOperatorUsername,
  pathname,
  enabled
}: {
  activeOperator: ReturnType<typeof useActiveOperator>;
  summaryOperatorUsername: string | null;
  pathname: string;
  enabled: boolean;
}) {
  if (!enabled) return null;
  if (!activeOperator.isPrivileged) return null;
  const { activeOperatorId, currentOperator } = activeOperator;
  const isImpersonating = activeOperatorId !== null && currentOperator !== null;
  // Profile / strategy editors hit `/operator/profile` + `/operator/strategy`
  // which are token-bound on the backend. Show a notice so the operator
  // understands why those screens still show their own data.
  const onProfile = pathname.startsWith(PROFILE_ROUTE_PATH);
  const onStrategy = pathname.startsWith(STRATEGY_ROUTE_PATH);
  const profileNotice = isImpersonating && (onProfile || onStrategy);

  if (!isImpersonating && !profileNotice) return null;

  const companyLabel = currentOperator
    ? currentOperator.company || currentOperator.full_name || currentOperator.username
    : summaryOperatorUsername;

  return (
    <div className="flex flex-col gap-2" aria-label="활성 운영자 컨텍스트">
      {isImpersonating ? (
        <div
          role="status"
          className="flex flex-wrap items-center gap-2 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-3 py-1.5 text-xs"
        >
          <Badge tone="info">보는 회사</Badge>
          <span className="font-medium text-[var(--color-fg)]">{companyLabel}</span>
          {currentOperator?.is_synthetic ? (
            <span className="text-[var(--color-muted)]">synthetic</span>
          ) : null}
          {currentOperator?.business_type ? (
            <span className="text-[var(--color-muted)]">· {currentOperator.business_type}</span>
          ) : null}
        </div>
      ) : null}
      {profileNotice ? (
        <div
          role="note"
          className="rounded-md border border-[var(--color-warn)] bg-[color-mix(in_oklch,var(--color-warn),white_88%)] px-3 py-2 text-xs text-[var(--color-fg)]"
        >
          현재 보는 회사: {companyLabel ?? "본인"} · 편집은 본인 회사로 돌아가야 가능합니다.
        </div>
      ) : null}
    </div>
  );
}
