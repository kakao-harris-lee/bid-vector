import { Card, CardContent } from "@/shared/components/ui";
import { AmountWithBasis } from "@/shared/components";
import { formatPercent } from "@/shared/lib";
import {
  BID_RATE_ON_BASE_LABEL,
  BID_RATE_ON_BASE_NOTE,
  BID_RATE_ON_ESTIMATE_LABEL,
  BID_RATE_ON_ESTIMATE_NOTE
} from "@/shared/constants/amountBasis";
import { t } from "@/shared/i18n";
import type { BidSummaryRecommendation } from "@/shared/types/bidSummary";

/**
 * 요약 최상단 — 추천 투찰금액(제출값) + 두 투찰률 + 가격 적합도(추정).
 *
 * 금액에는 basis 뱃지를, 투찰률에는 기준(기초금액/추정가격)을 라벨에 박아 낸다.
 * 기초금액 기준 율이 적격심사가 보는 율이고, 추정가격 대비 율은 참고 지표다.
 */
export function RecommendationHeadline({
  recommendation
}: {
  recommendation: BidSummaryRecommendation;
}) {
  return (
    <Card>
      <CardContent className="grid gap-4 pt-6 sm:grid-cols-3">
        <div className="flex flex-col gap-1 sm:col-span-2">
          {/* 제출값임을 금액 옆에 붙인다 — 기초금액 기준 산정이라 과세 공고면 부가세가
              이미 포함돼 있고, 추정가격에 투찰율을 곱해 검산하면 어긋난다. */}
          <AmountWithBasis
            amount={recommendation.recommended_amount}
            basis="submission"
            label={t("bid_summary.recommended_amount_label")}
            variant="hero"
          />
          <BidRateLine
            label={BID_RATE_ON_BASE_LABEL}
            rate={recommendation.recommended_bid_rate_on_base}
            note={BID_RATE_ON_BASE_NOTE}
          />
          <BidRateLine
            label={BID_RATE_ON_ESTIMATE_LABEL}
            rate={recommendation.recommended_bid_rate}
            note={BID_RATE_ON_ESTIMATE_NOTE}
          />
        </div>
        <div className="flex flex-col gap-1 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] p-3">
          <span className="text-xs text-[var(--color-muted)]">
            {t("bid_summary.probability_label")}
          </span>
          <strong className="text-2xl font-semibold tabular-nums text-[var(--color-fg)]">
            {formatPercent(recommendation.probability_score)}
          </strong>
          <span className="text-[11px] leading-tight text-[var(--color-muted)]">
            {t("bid_summary.probability_caveat")}
          </span>
        </div>
      </CardContent>
    </Card>
  );
}

/**
 * 투찰률 한 줄 — 기준을 라벨에 박아 두 율을 구분한다.
 *
 * 두 율을 "추천 투찰률" 한 이름으로 쓰면 적격심사가 보는 율(기초금액 대비)과 참고
 * 지표(추정가격 대비)가 뒤섞인다. 값이 없으면 아예 렌더하지 않는다.
 */
function BidRateLine({
  label,
  rate,
  note
}: {
  label: string;
  rate: number | null | undefined;
  note: string;
}) {
  if (typeof rate !== "number" || !Number.isFinite(rate)) return null;
  return (
    <span className="text-sm text-[var(--color-muted)]">
      {label}{" "}
      <strong className="tabular-nums text-[var(--color-fg)]">{formatPercent(rate)}</strong>
      <span className="ml-1 text-[11px] leading-tight">{note}</span>
    </span>
  );
}
