import { useCallback, useState } from "react";
import { ClipboardCopy, Download } from "lucide-react";
import { useShellContext } from "@/app/dashboardContext";
import {
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  toastApi
} from "@/shared/components/ui";
import { fetchBidFormDraftRaw } from "@/shared/api";
import { t } from "@/shared/i18n";
import type { BidFormDraftResponse } from "@/shared/types/bidFormDraft";
import { useBidFormDraftQuery } from "../bidSummaryHooks";
import { ERROR_BOX } from "./constants";

export function BidFormDraftCard({ decisionRecordId }: { decisionRecordId: number }) {
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
