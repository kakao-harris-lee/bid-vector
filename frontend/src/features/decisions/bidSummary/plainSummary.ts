import { formatCurrency, formatPercent } from "@/shared/lib";
import { t } from "@/shared/i18n";
import type { BidSummaryResponse } from "@/shared/types/bidSummary";

/** Build a plain-text copy of the summary for the clipboard / print fallback. */
export function buildPlainSummary(data: BidSummaryResponse): string {
  const { recommendation: rec, notice, prediction, category_floor, field_stat } = data;
  const lines: string[] = [];
  lines.push(`[${t("bid_summary.title")}]`);
  lines.push(`공고: ${notice.title}${notice.notice_number ? ` (${notice.notice_number})` : ""}`);
  lines.push(
    `${t("bid_summary.recommended_amount_label")}: ${formatCurrency(rec.recommended_amount)}`
  );
  if (rec.recommended_bid_rate != null) {
    lines.push(
      `${t("bid_summary.recommended_bid_rate_label")}: ${formatPercent(rec.recommended_bid_rate)}`
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
  if (field_stat) {
    lines.push(
      `${t("bid_summary.field_stat_close_rate_label")}: ${formatPercent(
        field_stat.est_price_close_rate
      )} (표본 ${field_stat.settled_count}건)`
    );
  }
  if (rec.reasoning) lines.push(`근거: ${rec.reasoning}`);
  lines.push("");
  lines.push(data.direct_submission_notice);
  return lines.join("\n");
}
