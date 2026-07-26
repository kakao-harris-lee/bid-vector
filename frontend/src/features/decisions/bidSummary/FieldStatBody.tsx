import { LabeledStat } from "@/shared/components";
import { formatPercent } from "@/shared/lib";
import { t } from "@/shared/i18n";
import type { BidSummaryFieldStat } from "@/shared/types/bidSummary";

export function FieldStatBody({ stat }: { stat: BidSummaryFieldStat }) {
  return (
    <>
      <div className="grid gap-3 sm:grid-cols-3">
        <LabeledStat variant="field"
          label={t("bid_summary.field_stat_close_rate_label")}
          value={formatPercent(stat.est_price_close_rate)}
        />
        <LabeledStat variant="field"
          label={t("bid_summary.field_stat_favorable_rate_label")}
          value={formatPercent(stat.eligible_favorable_rate)}
        />
        <LabeledStat variant="field"
          label={t("bid_summary.field_stat_sample_label")}
          value={`${stat.settled_count.toLocaleString("ko-KR")}건`}
        />
      </div>
      <p className="text-[11px] leading-tight text-[var(--color-muted)]">{stat.note}</p>
    </>
  );
}
