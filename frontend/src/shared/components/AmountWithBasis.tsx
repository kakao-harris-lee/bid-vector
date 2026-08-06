import { Badge } from "@/shared/components/ui";
import { formatCurrency, formatCurrencyCompact } from "@/shared/lib";
import {
  AMOUNT_BASIS_BADGE,
  AMOUNT_BASIS_LABEL,
  AMOUNT_BASIS_TONE,
  bidBaseSourceLabel,
  bidBaseSourceWarning,
  formatBaseToEstimateRatio,
  type AmountBasis
} from "@/shared/constants/amountBasis";

export type AmountWithBasisVariant = "block" | "inline" | "hero";

export interface AmountWithBasisProps {
  amount: number | null | undefined;
  /** 이 금액이 어느 기준의 값인지. 뱃지·기본 라벨이 여기서 나온다. */
  basis: AmountBasis;
  /**
   * 라벨을 덮어쓸 때만. 기본값은 basis 어휘이고, 바깥(예: 상세 행 `dt`)이 이미 같은
   * 라벨을 그리는 자리에서는 `null` 로 꺼서 중복 표기를 피한다.
   */
  label?: string | null;
  /** 기초금액 출처(provenance). `bid_base` 금액에서만 의미가 있다. */
  source?: string | null;
  /** 기초금액 ÷ 추정가격. 두 금액의 관측된 비(과세 여부 주장 아님). */
  ratio?: number | null;
  /** 서버가 실어 보낸 basis 안내 문구. 없으면 표시하지 않는다. */
  note?: string | null;
  variant?: AmountWithBasisVariant;
  /** 좁은 자리에서 축약 통화 포맷을 쓸 때. */
  compact?: boolean;
}

/**
 * 금액 + basis 뱃지 — 금액을 노출하는 모든 자리의 단일 출처.
 *
 * 추정가격(부가세 별도)과 투찰 기준금액(기초금액/사업금액)이 한 이름으로 뭉개진 것이
 * 실투찰 혼동의 원인이었으므로, 숫자만 훑어도 basis 가 붙어 보이게 뱃지를 항상 붙인다.
 * 화면마다 문구를 새로 짓지 않는다(§4.5-6).
 */
export function AmountWithBasis({
  amount,
  basis,
  label,
  source,
  ratio,
  note,
  variant = "block",
  compact = false
}: AmountWithBasisProps) {
  const text = compact ? formatCurrencyCompact(amount) : formatCurrency(amount);
  const badge = (
    <Badge tone={AMOUNT_BASIS_TONE[basis]}>{AMOUNT_BASIS_BADGE[basis]}</Badge>
  );

  if (variant === "inline") {
    return (
      <span className="inline-flex flex-wrap items-center gap-1">
        <span className="tabular-nums text-[var(--color-fg)]">{text}</span>
        {badge}
      </span>
    );
  }

  const sourceLabel = bidBaseSourceLabel(source);
  const ratioLabel = formatBaseToEstimateRatio(ratio);
  const provenance = [sourceLabel, ratioLabel].filter(Boolean).join(" · ");
  const sourceWarning = bidBaseSourceWarning(source);

  const resolvedLabel = label === null ? null : (label ?? AMOUNT_BASIS_LABEL[basis]);

  return (
    <div className="flex flex-col gap-0.5">
      {resolvedLabel !== null ? (
        <span className="text-xs text-[var(--color-muted)]">{resolvedLabel}</span>
      ) : null}
      <span className="flex flex-wrap items-center gap-2">
        <strong
          className={
            variant === "hero"
              ? "text-3xl font-bold tabular-nums text-[var(--color-fg)]"
              : "text-sm tabular-nums text-[var(--color-fg)]"
          }
        >
          {text}
        </strong>
        {badge}
      </span>
      {provenance ? (
        <span className="text-[11px] leading-tight text-[var(--color-muted)]">
          출처: {provenance}
        </span>
      ) : null}
      {sourceWarning ? (
        <span className="text-[11px] leading-tight text-[var(--color-warn)]">
          {sourceWarning}
        </span>
      ) : null}
      {note ? (
        <span className="text-[11px] leading-tight text-[var(--color-muted)]">{note}</span>
      ) : null}
    </div>
  );
}
