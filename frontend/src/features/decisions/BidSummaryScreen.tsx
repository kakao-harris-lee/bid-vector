import { useCallback, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, Copy, Printer } from "lucide-react";
import { useShellContext } from "@/app/dashboardContext";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  toastApi
} from "@/shared/components/ui";
import { formatCurrency, formatDateTime, formatPercent } from "@/shared/lib";
import { t } from "@/shared/i18n";
import type {
  BidSummaryFieldStat,
  BidSummaryResponse
} from "@/shared/types/bidSummary";
import { useBidSummaryQuery } from "./bidSummaryHooks";

const ERROR_BOX =
  "rounded-md border border-[var(--color-danger)] bg-[color-mix(in_oklch,var(--color-danger),white_85%)] px-3 py-2 text-sm text-[var(--color-danger)]";

export function BidSummaryScreen() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { session } = useShellContext();

  const decisionRecordId = id ? Number.parseInt(id, 10) : NaN;
  const idIsValid = Number.isFinite(decisionRecordId);

  const summary = useBidSummaryQuery(session, idIsValid ? decisionRecordId : null);

  const plainText = useMemo(
    () => (summary.data ? buildPlainSummary(summary.data) : ""),
    [summary.data]
  );

  const handleCopy = useCallback(async () => {
    if (!plainText) return;
    try {
      await navigator.clipboard.writeText(plainText);
      toastApi.success({ title: t("bid_summary.copy_success") });
    } catch {
      toastApi.danger({ title: t("bid_summary.copy_failure") });
    }
  }, [plainText]);

  const handlePrint = useCallback(() => {
    window.print();
  }, []);

  if (!idIsValid) {
    return (
      <p className={ERROR_BOX} role="alert">
        잘못된 결정 기록 id 입니다.
      </p>
    );
  }

  return (
    <section className="flex flex-col gap-4">
      <header className="flex flex-wrap items-center justify-between gap-2 print:hidden">
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => navigate(-1)}
            aria-label={t("bid_summary.back")}
          >
            <ArrowLeft className="h-4 w-4" />
            {t("bid_summary.back")}
          </Button>
        </div>
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={handleCopy}
            disabled={!summary.data}
          >
            <Copy className="h-4 w-4" />
            {t("bid_summary.copy")}
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={handlePrint}
            disabled={!summary.data}
          >
            <Printer className="h-4 w-4" />
            {t("bid_summary.print")}
          </Button>
        </div>
      </header>

      <div>
        <h2 className="text-lg font-semibold text-[var(--color-fg)]">
          {t("bid_summary.title")}
        </h2>
        <p className="text-xs text-[var(--color-muted)]">{t("bid_summary.subtitle")}</p>
      </div>

      {summary.isPending ? (
        <p className="text-sm text-[var(--color-muted)]">{t("bid_summary.loading")}</p>
      ) : null}

      {summary.error ? (
        <p className={ERROR_BOX} role="alert">
          {summary.error.message ?? t("bid_summary.error")}
        </p>
      ) : null}

      {summary.data ? <SummaryBody data={summary.data} /> : null}
    </section>
  );
}

function SummaryBody({ data }: { data: BidSummaryResponse }) {
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
              <Field
                label={t("bid_summary.predicted_price_label")}
                value={formatCurrency(prediction.predicted_price)}
              />
              <Field
                label={t("bid_summary.price_range_label")}
                value={
                  prediction.price_range_min != null || prediction.price_range_max != null
                    ? `${formatCurrency(prediction.price_range_min)} ~ ${formatCurrency(
                        prediction.price_range_max
                      )}`
                    : "-"
                }
              />
              <Field
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
          <Field
            label={t("bid_summary.priority_label")}
            value={formatPercent(recommendation.priority_score)}
          />
          <Field
            label={t("bid_summary.matched_label")}
            value={formatPercent(recommendation.matched_score)}
          />
          <Field
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
              <Field
                label={t("bid_summary.category_floor_rate_label")}
                value={formatPercent(category_floor.floor_bid_rate)}
              />
              <Field
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
          <Field label={t("bid_summary.notice_number_label")} value={notice.notice_number ?? "-"} full>
            <strong className="text-[var(--color-fg)]">{notice.title}</strong>
          </Field>
          <Field
            label={t("bid_summary.category_label")}
            value={notice.business_type_label ?? notice.category ?? "-"}
          />
          <Field
            label={t("bid_summary.budget_label")}
            value={formatCurrency(notice.budget_estimate)}
          />
          <Field label={t("bid_summary.agency_label")} value={notice.demand_agency ?? "-"} />
          <Field
            label={t("bid_summary.deadline_label")}
            value={notice.deadline ? formatDateTime(notice.deadline) : "-"}
          />
        </CardContent>
      </Card>
    </div>
  );
}

function FieldStatBody({ stat }: { stat: BidSummaryFieldStat }) {
  return (
    <>
      <div className="grid gap-3 sm:grid-cols-3">
        <Field
          label={t("bid_summary.field_stat_close_rate_label")}
          value={formatPercent(stat.est_price_close_rate)}
        />
        <Field
          label={t("bid_summary.field_stat_favorable_rate_label")}
          value={formatPercent(stat.eligible_favorable_rate)}
        />
        <Field
          label={t("bid_summary.field_stat_sample_label")}
          value={`${stat.settled_count.toLocaleString("ko-KR")}건`}
        />
      </div>
      <p className="text-[11px] leading-tight text-[var(--color-muted)]">{stat.note}</p>
    </>
  );
}

function Field({
  label,
  value,
  full = false,
  children
}: {
  label: string;
  value: string;
  full?: boolean;
  children?: React.ReactNode;
}) {
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

/** Build a plain-text copy of the summary for the clipboard / print fallback. */
function buildPlainSummary(data: BidSummaryResponse): string {
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
