import { Check, Clock, Loader2, Slash } from "lucide-react";
import { t } from "@/shared/i18n";
import { cn } from "@/shared/lib";
import type { BidDecisionActionType } from "@/shared/api";
import type { DashboardOpportunityItem } from "@/shared/types";

/**
 * Inline submit/review/skip action buttons rendered on dashboard cards.
 *
 * Compact variant lives inside `data-row` (height-constrained), the default
 * variant is used in the detail drawer where there is more room. Both share
 * the same mutation hook so the caller only needs to pass the click handler.
 *
 * The button matching the current decision_status gets an "active" badge tone
 * so the operator can see at a glance which transition is already in effect;
 * clicking it again is allowed (the backend treats it as a re-affirmation).
 */
export interface InlineActionButtonsProps {
  decisionStatus: DashboardOpportunityItem["decision_status"];
  pendingAction: BidDecisionActionType | null;
  disabled?: boolean;
  compact?: boolean;
  onAction: (action: BidDecisionActionType) => void;
}

interface ActionDescriptor {
  key: BidDecisionActionType;
  label: string;
  ariaLabel: string;
  activeStatus: DashboardOpportunityItem["decision_status"];
  icon: typeof Check;
  activeClass: string;
}

const ACTIONS: ActionDescriptor[] = [
  {
    key: "submit",
    label: t("dashboard.inline_actions.submit"),
    ariaLabel: t("dashboard.inline_actions.submit_aria"),
    activeStatus: "submitted",
    icon: Check,
    activeClass:
      "border-[var(--color-success)] bg-[color-mix(in_oklch,var(--color-success),white_82%)] text-[color-mix(in_oklch,var(--color-success),black_30%)]"
  },
  {
    key: "review",
    label: t("dashboard.inline_actions.review"),
    ariaLabel: t("dashboard.inline_actions.review_aria"),
    activeStatus: "reviewing",
    icon: Clock,
    activeClass:
      "border-[var(--color-warn)] bg-[color-mix(in_oklch,var(--color-warn),white_78%)] text-[color-mix(in_oklch,var(--color-warn),black_40%)]"
  },
  {
    key: "skip",
    label: t("dashboard.inline_actions.skip"),
    ariaLabel: t("dashboard.inline_actions.skip_aria"),
    activeStatus: "skipped",
    icon: Slash,
    activeClass:
      "border-[var(--color-muted)] bg-[var(--color-secondary)] text-[var(--color-secondary-foreground)]"
  }
];

export function InlineActionButtons({
  decisionStatus,
  pendingAction,
  disabled = false,
  compact = false,
  onAction
}: InlineActionButtonsProps) {
  const sizeClass = compact
    ? "px-2 py-1 text-[11px] gap-1"
    : "px-3 py-1.5 text-xs gap-1.5";
  const iconSize = compact ? 12 : 14;
  const baseClass =
    "inline-flex items-center rounded-md border border-[var(--color-border)] bg-[var(--color-card)] font-medium text-[var(--color-fg)] transition-colors hover:border-[var(--color-primary)] disabled:cursor-not-allowed disabled:opacity-60";

  return (
    <div
      className={cn("flex items-center", compact ? "gap-1" : "gap-2")}
      role="group"
      aria-label={t("dashboard.inline_actions.label")}
      onClick={(event) => event.stopPropagation()}
      onMouseDown={(event) => event.stopPropagation()}
    >
      {ACTIONS.map((descriptor) => {
        const isActive = descriptor.activeStatus === decisionStatus;
        const isPending = pendingAction === descriptor.key;
        const isAnyPending = pendingAction !== null;
        const Icon = descriptor.icon;
        return (
          <button
            key={descriptor.key}
            type="button"
            aria-label={descriptor.ariaLabel}
            aria-pressed={isActive}
            disabled={disabled || isAnyPending}
            className={cn(
              baseClass,
              sizeClass,
              isActive && descriptor.activeClass
            )}
            onClick={(event) => {
              event.stopPropagation();
              event.preventDefault();
              onAction(descriptor.key);
            }}
          >
            {isPending ? (
              <Loader2
                size={iconSize}
                className="animate-spin"
                aria-hidden="true"
              />
            ) : (
              <Icon size={iconSize} aria-hidden="true" />
            )}
            <span>{descriptor.label}</span>
          </button>
        );
      })}
    </div>
  );
}
