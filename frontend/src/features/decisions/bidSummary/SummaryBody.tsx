import { Badge, Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import { FloorShortfallStat, LabeledStat } from "@/shared/components";
import { formatCurrency, formatPercent } from "@/shared/lib";
import { t } from "@/shared/i18n";
import type { BidSummaryResponse } from "@/shared/types/bidSummary";
import { FieldStatBody } from "./FieldStatBody";
import { NoticeMetaCard } from "./NoticeMetaCard";
import { RecommendationHeadline } from "./RecommendationHeadline";

export function SummaryBody({ data }: { data: BidSummaryResponse }) {
  const { recommendation, prediction, category_floor, field_stat, notice } = data;

  return (
    <div className="flex flex-col gap-4">
      {/* 직접 제출 안내 — 가장 먼저, 눈에 띄게. */}
      <div
        role="note"
        className="rounded-md border-2 border-[var(--color-warn)] bg-[color-mix(in_oklch,var(--color-warn),white_82%)] px-4 py-3"
      >
        <p className="text-xs font-semibold uppercase tracking-wide text-[color-mix(in_oklch,var(--color-warn),black_40%)]">
          {t("bid_summary.direct_submission_title")}
        </p>
        <p className="mt-1 text-sm text-[var(--color-fg)]">
          {data.direct_submission_notice}
        </p>
      </div>

      {/* 추천 투찰금액 / 두 투찰률 / 가격 적합도(추정) — 크게. */}
      <RecommendationHeadline recommendation={recommendation} />

      {/* 예측 가격대 */}
      <Card>
        <CardHeader>
          <CardTitle>{t("bid_summary.prediction_title")}</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3 text-sm">
          {prediction ? (
            <div className="grid gap-3 sm:grid-cols-3">
              <LabeledStat variant="field"
                label={t("bid_summary.predicted_price_label")}
                value={formatCurrency(prediction.predicted_price)}
              />
              <LabeledStat variant="field"
                label={t("bid_summary.price_range_label")}
                value={
                  prediction.price_range_min != null || prediction.price_range_max != null
                    ? `${formatCurrency(prediction.price_range_min)} ~ ${formatCurrency(
                        prediction.price_range_max
                      )}`
                    : "-"
                }
              />
              <LabeledStat variant="field"
                label={t("bid_summary.confidence_label")}
                value={formatPercent(prediction.confidence_score)}
              />
            </div>
          ) : (
            <p className="text-[var(--color-muted)]">{t("bid_summary.prediction_empty")}</p>
          )}
        </CardContent>
      </Card>

      {/* 참고 점수 */}
      <Card>
        <CardHeader>
          <CardTitle>{t("bid_summary.scores_title")}</CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-3 gap-3 text-sm">
          <LabeledStat variant="field"
            label={t("bid_summary.priority_label")}
            value={formatPercent(recommendation.priority_score)}
          />
          <LabeledStat variant="field"
            label={t("bid_summary.matched_label")}
            value={formatPercent(recommendation.matched_score)}
          />
          <LabeledStat variant="field"
            label={t("bid_summary.competitiveness_label")}
            value={formatPercent(recommendation.competitiveness_score)}
          />
        </CardContent>
      </Card>

      {/* 판단 근거 / 강점 / 리스크 */}
      <Card>
        <CardHeader>
          <CardTitle>{t("bid_summary.reasoning_title")}</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4 text-sm">
          {recommendation.reasoning ? (
            <p className="whitespace-pre-line text-[var(--color-fg)]">
              {recommendation.reasoning}
            </p>
          ) : null}
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <p className="mb-1 text-xs font-semibold text-[var(--color-muted)]">
                {t("bid_summary.strengths_title")}
              </p>
              {recommendation.strengths.length > 0 ? (
                <ul className="flex flex-col gap-1">
                  {recommendation.strengths.map((item) => (
                    <li key={item} className="flex items-start gap-1">
                      <Badge tone="healthy">강점</Badge>
                      <span className="text-[var(--color-fg)]">{item}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-[var(--color-muted)]">
                  {t("bid_summary.strengths_empty")}
                </p>
              )}
            </div>
            <div>
              <p className="mb-1 text-xs font-semibold text-[var(--color-muted)]">
                {t("bid_summary.risks_title")}
              </p>
              {recommendation.risk_flags.length > 0 ? (
                <ul className="flex flex-col gap-1">
                  {recommendation.risk_flags.map((item) => (
                    <li key={item} className="flex items-start gap-1">
                      <Badge tone="critical">리스크</Badge>
                      <span className="text-[var(--color-fg)]">{item}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-[var(--color-muted)]">{t("bid_summary.risks_empty")}</p>
              )}
            </div>
          </div>
        </CardContent>
      </Card>

      {/* 카테고리 낙찰하한율 (참고) */}
      <Card>
        <CardHeader>
          <CardTitle>{t("bid_summary.category_floor_title")}</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-2 text-sm">
          <p className="text-xs text-[var(--color-warn)]">
            {t("bid_summary.category_floor_predisclosure")}
          </p>
          {category_floor.floor_bid_rate !== null &&
          category_floor.floor_bid_rate !== undefined ? (
            <div className="grid gap-3 sm:grid-cols-2">
              <LabeledStat variant="field"
                label={t("bid_summary.category_floor_rate_label")}
                value={formatPercent(category_floor.floor_bid_rate)}
              />
              <LabeledStat variant="field"
                label={t("bid_summary.category_floor_price_label")}
                value={formatCurrency(category_floor.floor_price)}
              />
            </div>
          ) : (
            <p className="text-[var(--color-muted)]">
              {t("bid_summary.category_floor_unset")}
            </p>
          )}
          <p className="text-[11px] leading-tight text-[var(--color-muted)]">
            {category_floor.note}
          </p>
          {/* 추천가가 하한 아래로 갈렸던 과거 빈도 — 하한 이야기 바로 옆에 둔다.
              추천가는 기초금액 기준이고 실격 하한은 추첨된 예정가 기준이라, 같은
              추천가도 사정률 추첨에 따라 하한 위/아래로 갈린다. */}
          <FloorShortfallStat estimate={data.floor_shortfall} />
        </CardContent>
      </Card>

      {/* 분야 통계 (있을 때만) */}
      <Card>
        <CardHeader>
          <CardTitle>{t("bid_summary.field_stat_title")}</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-2 text-sm">
          {field_stat ? (
            <FieldStatBody stat={field_stat} />
          ) : (
            <p className="text-[var(--color-muted)]">{t("bid_summary.field_stat_empty")}</p>
          )}
        </CardContent>
      </Card>

      {/* 공고 메타 — 추정가격과 투찰 기준금액을 각각의 행으로. */}
      <NoticeMetaCard notice={notice} />
    </div>
  );
}
