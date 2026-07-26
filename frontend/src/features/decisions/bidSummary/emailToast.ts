import { toastApi, type ToastTone } from "@/shared/components/ui";
import type { BidReportEmailDeliveryResponse } from "@/shared/api";
import { t } from "@/shared/i18n";

/** Tone → toastApi method dispatch (avoids a switch over the honesty states). */
export const TOAST_BY_TONE: Record<ToastTone, (o: { title: string; description?: string }) => number> = {
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
export function buildEmailToast(result: BidReportEmailDeliveryResponse): {
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
