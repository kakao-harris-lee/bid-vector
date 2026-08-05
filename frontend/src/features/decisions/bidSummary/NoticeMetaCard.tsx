import { Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import { AmountWithBasis, LabeledStat } from "@/shared/components";
import { formatDateTime } from "@/shared/lib";
import { AMOUNT_BASIS_LABEL } from "@/shared/constants/amountBasis";
import { t } from "@/shared/i18n";
import type { BidSummaryNoticeMeta } from "@/shared/types/bidSummary";

/**
 * 공고 정보 카드 — 추정가격과 투찰 기준금액을 **각각의 행**으로 낸다.
 *
 * 두 금액을 "예산" 한 줄로 묶어 보여준 표기가 실투찰 혼동의 출발점이었다. 기초금액
 * 쪽에는 출처(provenance)와 서버가 준 basis 안내 문구를 함께 붙인다.
 */
export function NoticeMetaCard({ notice }: { notice: BidSummaryNoticeMeta }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("bid_summary.notice_title")}</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-3 text-sm sm:grid-cols-2">
        <LabeledStat
          variant="field"
          label={t("bid_summary.notice_number_label")}
          value={notice.notice_number ?? "-"}
          full
        >
          <strong className="text-[var(--color-fg)]">{notice.title}</strong>
        </LabeledStat>
        <LabeledStat
          variant="field"
          label={t("bid_summary.category_label")}
          value={notice.business_type_label ?? notice.category ?? "-"}
        />
        <AmountWithBasis
          amount={notice.budget_estimate}
          basis="estimate"
          label={AMOUNT_BASIS_LABEL.estimate}
        />
        <AmountWithBasis
          amount={notice.bid_base_amount}
          basis="bid_base"
          label={AMOUNT_BASIS_LABEL.bid_base}
          source={notice.bid_base_source}
          ratio={notice.bid_base_to_estimate_ratio}
          note={notice.bid_base_note}
        />
        <LabeledStat
          variant="field"
          label={t("bid_summary.agency_label")}
          value={notice.demand_agency ?? "-"}
        />
        <LabeledStat
          variant="field"
          label={t("bid_summary.deadline_label")}
          value={notice.deadline ? formatDateTime(notice.deadline) : "-"}
        />
      </CardContent>
    </Card>
  );
}
