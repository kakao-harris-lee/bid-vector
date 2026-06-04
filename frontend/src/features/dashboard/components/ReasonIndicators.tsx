import { t } from "@/shared/i18n";

export interface ReasonIndicatorsProps {
  strengths?: string[] | null;
  riskFlags?: string[] | null;
  className?: string;
}

/**
 * 리스트 행용 컴팩트 인디케이터.
 * 추구 가능 근거 개수(✅N)와 리스크 신호 개수(⚠️M)를 작게 표시한다.
 * 둘 다 0이면 아무것도 렌더하지 않는다.
 */
export function ReasonIndicators({ strengths, riskFlags, className }: ReasonIndicatorsProps) {
  const strengthCount = strengths?.length ?? 0;
  const riskCount = riskFlags?.length ?? 0;

  if (strengthCount === 0 && riskCount === 0) return null;

  return (
    <span
      className={`inline-flex items-center gap-1.5 text-[10px] leading-none ${className ?? ""}`}
      aria-label={t("decision_reasons.title")}
    >
      {strengthCount > 0 ? (
        <span
          className="inline-flex items-center gap-0.5 rounded-full bg-[color-mix(in_oklch,var(--color-success),white_80%)] px-1.5 py-0.5 text-[color-mix(in_oklch,var(--color-success),black_30%)]"
          aria-label={`${t("decision_reasons.indicator_strengths_label")} ${strengthCount}`}
        >
          <span aria-hidden="true">✅</span>
          {strengthCount}
        </span>
      ) : null}
      {riskCount > 0 ? (
        <span
          className="inline-flex items-center gap-0.5 rounded-full bg-[color-mix(in_oklch,var(--color-warn),white_78%)] px-1.5 py-0.5 text-[color-mix(in_oklch,var(--color-warn),black_45%)]"
          aria-label={`${t("decision_reasons.indicator_risks_label")} ${riskCount}`}
        >
          <span aria-hidden="true">⚠️</span>
          {riskCount}
        </span>
      ) : null}
    </span>
  );
}
