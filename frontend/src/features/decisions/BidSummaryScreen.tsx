import { useCallback, useMemo } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, Copy, Loader2, Mail, Printer } from "lucide-react";
import { useShellContext } from "@/app/dashboardContext";
import { Button, toastApi } from "@/shared/components/ui";
import { ApiError } from "@/shared/api";
import { t } from "@/shared/i18n";
import {
  useBidSummaryQuery,
  useSendBidReportEmailMutation
} from "./bidSummaryHooks";
import {
  BidFormDraftCard,
  ERROR_BOX,
  SummaryBody,
  TOAST_BY_TONE,
  buildEmailToast,
  buildPlainSummary
} from "./bidSummary";

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
