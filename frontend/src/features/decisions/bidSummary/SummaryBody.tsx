import { Badge, Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import { LabeledStat } from "@/shared/components";
import { formatCurrency, formatDateTime, formatPercent } from "@/shared/lib";
import { t } from "@/shared/i18n";
import type { BidSummaryResponse } from "@/shared/types/bidSummary";
import { FieldStatBody } from "./FieldStatBody";

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

      {/* 추천 투찰가 / 투찰률 — 크게. */}
      <Card>
        <CardContent className="grid gap-4 pt-6 sm:grid-cols-3">
          <div className="flex flex-col gap-1 sm:col-span-2">
            <span className="text-xs text-[var(--color-muted)]">
              {t("bid_summary.recommended_amount_label")}
            </span>
            <strong className="text-3xl font-bold tabular-nums text-[var(--color-fg)]">
              {formatCurrency(recommendation.recommended_amount)}
            </strong>
            {recommendation.recommended_bid_rate !== null &&
            recommendation.recommended_bid_rate !== undefined ? (
              <span className="text-sm text-[var(--color-muted)]">
                {t("bid_summary.recommended_bid_rate_label")}{" "}
                <strong className="tabular-nums text-[var(--color-fg)]">
                  {formatPercent(recommendation.recommended_bid_rate)}
                </strong>
              </span>
            ) : null}
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

      {/* 공고 메타 */}
      <Card>
        <CardHeader>
          <CardTitle>{t("bid_summary.notice_title")}</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 text-sm sm:grid-cols-2">
          <LabeledStat variant="field" label={t("bid_summary.notice_number_label")} value={notice.notice_number ?? "-"} full>
            <strong className="text-[var(--color-fg)]">{notice.title}</strong>
          </LabeledStat>
          <LabeledStat variant="field"
            label={t("bid_summary.category_label")}
            value={notice.business_type_label ?? notice.category ?? "-"}
          />
          <LabeledStat variant="field"
            label={t("bid_summary.budget_label")}
            value={formatCurrency(notice.budget_estimate)}
          />
          <LabeledStat variant="field" label={t("bid_summary.agency_label")} value={notice.demand_agency ?? "-"} />
          <LabeledStat variant="field"
            label={t("bid_summary.deadline_label")}
            value={notice.deadline ? formatDateTime(notice.deadline) : "-"}
          />
        </CardContent>
      </Card>
    </div>
  );
}
