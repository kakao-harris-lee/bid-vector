import { useCallback, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeft,
  ClipboardCopy,
  Copy,
  Download,
  Loader2,
  Mail,
  Printer
} from "lucide-react";
import { useShellContext } from "@/app/dashboardContext";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  toastApi,
  type ToastTone
} from "@/shared/components/ui";
import { LabeledStat } from "@/shared/components";
import { ApiError, fetchBidFormDraftRaw } from "@/shared/api";
import type { BidReportEmailDeliveryResponse } from "@/shared/api";
import { formatCurrency, formatDateTime, formatPercent } from "@/shared/lib";
import { t } from "@/shared/i18n";
import type {
  BidSummaryFieldStat,
  BidSummaryResponse
} from "@/shared/types/bidSummary";
import type { BidFormDraftResponse } from "@/shared/types/bidFormDraft";
import {
  useBidFormDraftQuery,
  useBidSummaryQuery,
  useSendBidReportEmailMutation
} from "./bidSummaryHooks";

const ERROR_BOX =
  "rounded-md border border-[var(--color-danger)] bg-[color-mix(in_oklch,var(--color-danger),white_85%)] px-3 py-2 text-sm text-[var(--color-danger)]";

/** Tone → toastApi method dispatch (avoids a switch over the honesty states). */
const TOAST_BY_TONE: Record<ToastTone, (o: { title: string; description?: string }) => number> = {
  info: toastApi.info,
  success: toastApi.success,
  warning: toastApi.warning,
  danger: toastApi.danger
};

type EmailStatusKey = "dry_run_rendered" | "sent" | "skipped_no_recipient" | "failed";

/** Per-status toast tone + i18n title key — declared as data, not branch tree. */
const EMAIL_STATUS_TOAST: Record<EmailStatusKey, { tone: ToastTone; titleKey: string }> = {
  dry_run_rendered: { tone: "warning", titleKey: "bid_report_email.dry_run_title" },
  sent: { tone: "success", titleKey: "bid_report_email.sent_title" },
  skipped_no_recipient: { tone: "warning", titleKey: "bid_report_email.skipped_title" },
  failed: { tone: "danger", titleKey: "bid_report_email.failed_title" }
};

/**
 * 정직(§2) 가드: dry-run 응답은 절대 '메일을 보냈습니다(sent)'로 표시하지 않는다.
 * `delivery_status` 를 신뢰하되 `dry_run === true` 이면 sent 를 미리보기로 강등하고,
 * 계약 밖 값은 미리보기(dry_run_rendered)로 안전하게 수렴시킨다.
 */
function resolveEmailStatusKey(result: BidReportEmailDeliveryResponse): EmailStatusKey {
  if (result.delivery_status === "sent") {
    return result.dry_run ? "dry_run_rendered" : "sent";
  }
  if (
    result.delivery_status === "skipped_no_recipient" ||
    result.delivery_status === "failed"
  ) {
    return result.delivery_status;
  }
  return "dry_run_rendered";
}

/** Build the honest toast payload (tone/title/description) for an email delivery result. */
function buildEmailToast(result: BidReportEmailDeliveryResponse): {
  tone: ToastTone;
  title: string;
  description?: string;
} {
  const key = resolveEmailStatusKey(result);
  const { tone, titleKey } = EMAIL_STATUS_TOAST[key];
  const detail = `${t("bid_report_email.recipient_label")} ${result.masked_recipient} · ${t(
    "bid_report_email.subject_label"
  )} ${result.subject}`;
  if (key === "dry_run_rendered") {
    return {
      tone,
      title: t(titleKey),
      description: `${t("bid_report_email.dry_run_description")} ${detail}`
    };
  }
  if (key === "skipped_no_recipient") {
    return { tone, title: t(titleKey) };
  }
  return { tone, title: t(titleKey), description: detail };
}

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

  const emailMutation = useSendBidReportEmailMutation(session);
  const handleSendEmail = useCallback(() => {
    if (!idIsValid) return;
    emailMutation.mutate(decisionRecordId, {
      onSuccess: (result) => {
        const { tone, title, description } = buildEmailToast(result);
        TOAST_BY_TONE[tone]({ title, description });
      },
      onError: (error) => {
        // 401 은 세션 만료 모달이 처리한다 — 조용히 무시(siblings 와 동일).
        if (error instanceof ApiError && error.status === 401) return;
        toastApi.danger({
          title: t("bid_report_email.error_title"),
          description: error.message || t("bid_report_email.error_description")
        });
      }
    });
  }, [emailMutation, decisionRecordId, idIsValid]);

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
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={handleSendEmail}
            disabled={!summary.data || emailMutation.isPending}
          >
            {emailMutation.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : (
              <Mail className="h-4 w-4" aria-hidden="true" />
            )}
            {t("bid_report_email.button")}
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

      {summary.data ? (
        <BidFormDraftCard decisionRecordId={decisionRecordId} />
      ) : null}
    </section>
  );
}

function BidFormDraftCard({ decisionRecordId }: { decisionRecordId: number }) {
  const { session } = useShellContext();
  const token = session?.token ?? null;
  const draft = useBidFormDraftQuery(session, decisionRecordId);
  const [busy, setBusy] = useState(false);

  const handleDownloadCsv = useCallback(async () => {
    setBusy(true);
    try {
      const csv = await fetchBidFormDraftRaw(decisionRecordId, "csv", token);
      const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `bid-form-draft-${decisionRecordId}.csv`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch {
      toastApi.danger({ title: t("bid_form_draft.download_failure") });
    } finally {
      setBusy(false);
    }
  }, [decisionRecordId, token]);

  const handleCopyDraft = useCallback(async () => {
    setBusy(true);
    try {
      const text = await fetchBidFormDraftRaw(decisionRecordId, "text", token);
      await navigator.clipboard.writeText(text);
      toastApi.success({ title: t("bid_form_draft.copy_success") });
    } catch {
      toastApi.danger({ title: t("bid_form_draft.copy_failure") });
    } finally {
      setBusy(false);
    }
  }, [decisionRecordId, token]);

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-2">
        <div className="flex flex-col gap-0.5">
          <CardTitle>{t("bid_form_draft.title")}</CardTitle>
          <p className="text-xs text-[var(--color-muted)]">
            {t("bid_form_draft.subtitle")}
          </p>
        </div>
        <div className="flex items-center gap-2 print:hidden">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={handleDownloadCsv}
            disabled={!draft.data || busy}
          >
            <Download className="h-4 w-4" />
            {t("bid_form_draft.download_csv")}
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={handleCopyDraft}
            disabled={!draft.data || busy}
          >
            <ClipboardCopy className="h-4 w-4" />
            {t("bid_form_draft.copy")}
          </Button>
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-4 text-sm">
        {draft.isPending ? (
          <p className="text-[var(--color-muted)]">{t("bid_form_draft.loading")}</p>
        ) : null}

        {draft.error ? (
          <p className={ERROR_BOX} role="alert">
            {draft.error.message ?? t("bid_form_draft.error")}
          </p>
        ) : null}

        {draft.data ? <BidFormDraftBody data={draft.data} /> : null}
      </CardContent>
    </Card>
  );
}

function BidFormDraftBody({ data }: { data: BidFormDraftResponse }) {
  return (
    <div className="flex flex-col gap-4">
      {/* 직접 제출 안내 — 자동 제출이 아님을 눈에 띄게. */}
      <div
        role="note"
        className="rounded-md border-2 border-[var(--color-warn)] bg-[color-mix(in_oklch,var(--color-warn),white_82%)] px-4 py-3"
      >
        <p className="text-xs font-semibold uppercase tracking-wide text-[color-mix(in_oklch,var(--color-warn),black_40%)]">
          {t("bid_form_draft.direct_submission_title")}
        </p>
        <p className="mt-1 text-sm text-[var(--color-fg)]">
          {data.direct_submission_notice}
        </p>
      </div>

      {/* 적격여부(추정) — 정직 라벨 + caveat */}
      <div className="flex flex-col gap-0.5">
        <span className="text-xs text-[var(--color-muted)]">
          {t("bid_form_draft.eligibility_label")}
        </span>
        <strong className="text-sm text-[var(--color-fg)]">
          {data.eligibility_estimate}
        </strong>
        <span className="text-[11px] leading-tight text-[var(--color-muted)]">
          {data.eligibility_note}
        </span>
      </div>

      {/* 나라장터 입력 항목 매핑표 — 운영자가 그대로 입력 */}
      <div className="flex flex-col gap-2">
        <p className="text-xs font-semibold text-[var(--color-muted)]">
          {t("bid_form_draft.fields_title")}
        </p>
        <dl className="grid gap-3 sm:grid-cols-2">
          {data.fields.map((field) => (
            <div key={field.key} className="flex flex-col gap-0.5">
              <dt className="text-xs text-[var(--color-muted)]">{field.field_label}</dt>
              <dd className="text-sm tabular-nums text-[var(--color-fg)]">
                {field.value || "-"}
              </dd>
              {field.note ? (
                <p className="text-[11px] leading-tight text-[var(--color-muted)]">
                  {field.note}
                </p>
              ) : null}
            </div>
          ))}
        </dl>
      </div>
    </div>
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

function FieldStatBody({ stat }: { stat: BidSummaryFieldStat }) {
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
