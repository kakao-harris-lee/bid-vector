import type { ReactNode } from "react";

export type LabeledStatVariant =
  | "stat"
  | "metric"
  | "card"
  | "evidence"
  | "field"
  | "guide";

export interface LabeledStatProps {
  label: string;
  value: string;
  /**
   * 시각 variant. 각 화면이 따로 정의하던 presentational stat을 한 컴포넌트로
   * 모으면서, 원본 마크업을 그대로 보존하기 위해 variant로 분기합니다.
   * - `stat`     : OperationsScreen (break-words, min-w-0)
   * - `metric`   : DecisionsScreen 결과 메트릭 (text-sm strong)
   * - `card`     : SampleReportView (테두리 카드 + truncate)
   * - `evidence` : DecisionsScreen 증적(dt/dd)
   * - `field`    : BidSummaryScreen (full 폭 + children 슬롯)
   * - `guide`    : StrategyEditor 가이드 카드 (테두리 카드 + detail 보조줄)
   */
  variant?: LabeledStatVariant;
  /** `field` variant 전용: 2열 그리드에서 전체 폭 차지. */
  full?: boolean;
  /** `guide` variant 전용: value 아래 보조 설명줄. */
  detail?: string;
  /** `field` variant 전용: value 자리에 커스텀 노드를 넣고 value는 보조 텍스트로. */
  children?: ReactNode;
}

/**
 * `{label, value}` presentational stat의 단일 출처.
 *
 * OperationsScreen·SampleReportView·DecisionsScreen·BidSummaryScreen이
 * 각자 정의하던 거의 동일한 stat 컴포넌트를 통합한 것입니다. 마크업은
 * variant별로 원본과 동일하게 유지합니다.
 */
export function LabeledStat({
  label,
  value,
  variant = "stat",
  full = false,
  detail,
  children
}: LabeledStatProps) {
  if (variant === "guide") {
    return (
      <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-card)] p-3">
        <span className="text-[var(--color-muted)]">{label}</span>
        <strong className="mt-0.5 block text-sm text-[var(--color-fg)]">{value}</strong>
        {detail !== undefined ? (
          <p className="mt-1 text-[var(--color-muted)]">{detail}</p>
        ) : null}
      </div>
    );
  }

  if (variant === "card") {
    return (
      <div className="rounded-md border border-[var(--color-border)] px-2 py-1.5">
        <div className="text-[var(--color-muted)]">{label}</div>
        <div className="truncate font-medium text-[var(--color-fg)]">{value}</div>
      </div>
    );
  }

  if (variant === "evidence") {
    return (
      <div>
        <dt className="text-[var(--color-muted)]">{label}</dt>
        <dd className="font-medium tabular-nums text-[var(--color-fg)]">{value}</dd>
      </div>
    );
  }

  if (variant === "metric") {
    return (
      <div className="flex flex-col gap-0.5">
        <span className="text-[var(--color-muted)]">{label}</span>
        <strong className="text-sm text-[var(--color-fg)] tabular-nums">{value}</strong>
      </div>
    );
  }

  if (variant === "field") {
    return (
      <div className={`flex flex-col gap-0.5 ${full ? "sm:col-span-2" : ""}`}>
        <span className="text-xs text-[var(--color-muted)]">{label}</span>
        {children ?? (
          <span className="text-sm tabular-nums text-[var(--color-fg)]">{value}</span>
        )}
        {children ? <span className="text-xs text-[var(--color-muted)]">{value}</span> : null}
      </div>
    );
  }

  // variant === "stat"
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <span className="break-words text-[var(--color-muted)]">{label}</span>
      <strong className="break-words tabular-nums text-[var(--color-fg)]">{value}</strong>
    </div>
  );
}
