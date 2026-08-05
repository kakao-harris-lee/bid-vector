import { formatCurrency, formatPercent } from "@/shared/lib";
import {
  AMOUNT_BASIS_BADGE,
  AMOUNT_BASIS_LABEL,
  BID_RATE_ON_BASE_LABEL,
  BID_RATE_ON_ESTIMATE_LABEL
} from "@/shared/constants/amountBasis";
import { FLOOR_SHORTFALL_TITLE, describeFloorShortfall } from "@/shared/floorShortfall";
import { t } from "@/shared/i18n";
import type { BidSummaryResponse } from "@/shared/types/bidSummary";

/**
 * Build a plain-text copy of the summary for the clipboard / print fallback.
 *
 * 화면에서 basis 를 분리해 보여줘도 복사본이 "예산 / 추천 투찰가" 두 줄로 뭉개지면
 * 혼동이 그대로 따라간다. 복사본도 두 금액과 두 투찰률을 기준까지 적어 낸다.
 */
export function buildPlainSummary(data: BidSummaryResponse): string {
  const { recommendation: rec, notice, prediction, category_floor, field_stat } = data;
  const lines: string[] = [];
  lines.push(`[${t("bid_summary.title")}]`);
  lines.push(`공고: ${notice.title}${notice.notice_number ? ` (${notice.notice_number})` : ""}`);
  lines.push(`${AMOUNT_BASIS_LABEL.estimate}: ${formatCurrency(notice.budget_estimate)}`);
  lines.push(`${AMOUNT_BASIS_LABEL.bid_base}: ${formatCurrency(notice.bid_base_amount)}`);
  lines.push(
    `${t("bid_summary.recommended_amount_label")}(${AMOUNT_BASIS_BADGE.submission}): ` +
      formatCurrency(rec.recommended_amount)
  );
  if (rec.recommended_bid_rate_on_base != null) {
    lines.push(
      `${BID_RATE_ON_BASE_LABEL}: ${formatPercent(rec.recommended_bid_rate_on_base)}`
    );
  }
  if (rec.recommended_bid_rate != null) {
    lines.push(
      `${BID_RATE_ON_ESTIMATE_LABEL}: ${formatPercent(rec.recommended_bid_rate)}`
    );
  }
  lines.push(
    `${t("bid_summary.probability_label")}: ${formatPercent(rec.probability_score)} (${t(
      "bid_summary.probability_caveat"
    )})`
  );
  if (prediction) {
    lines.push(
      `${t("bid_summary.predicted_price_label")}: ${formatCurrency(prediction.predicted_price)}`
    );
  }
  if (category_floor.floor_bid_rate != null) {
    lines.push(
      `${t("bid_summary.category_floor_title")}: ${formatPercent(category_floor.floor_bid_rate)}`
    );
  }
  const shortfall = describeFloorShortfall(data.floor_shortfall);
  lines.push(
    `${FLOOR_SHORTFALL_TITLE}: ${shortfall.headline}` +
      (shortfall.detail ? ` (${shortfall.detail})` : "")
  );
  if (field_stat) {
    lines.push(
      `${t("bid_summary.field_stat_close_rate_label")}: ${formatPercent(
        field_stat.est_price_close_rate
      )} (표본 ${field_stat.settled_count}건)`
    );
  }
  if (rec.reasoning) lines.push(`근거: ${rec.reasoning}`);
  lines.push("");
  lines.push(notice.bid_base_note);
  lines.push(shortfall.note);
  lines.push(data.direct_submission_notice);
  return lines.join("\n");
}
