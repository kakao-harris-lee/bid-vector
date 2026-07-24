import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { AlertTriangle, ArrowRight, Info, ShieldCheck } from "lucide-react";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle
} from "@/shared/components/ui";
import { formatDateTime } from "@/shared/lib";
import { t } from "@/shared/i18n";
import type { AuthSession } from "@/app/layout/AuthGate";
import type { BusinessVerificationResponse } from "@/shared/api";
import { businessVerificationSchema, type BusinessVerificationFormValues } from "./schema";
import { STEP_META, businessStatusMeta } from "./constants";
import { useVerifyBusinessNumberMutation } from "./hooks";

export interface BusinessStepProps {
  session: AuthSession | null;
  /** 다음 단계(seed)로 진행 — 검증은 필수가 아니라 항상 진행 가능. */
  onProceed: () => void;
  /** 사업자번호 없이 시작 — seed 로 직행. */
  onSkip: () => void;
}

const EMPTY_DEFAULTS: BusinessVerificationFormValues = {
  business_number: "",
  start_date: "",
  representative_name: ""
};

/** 상태 라벨은 i18n 룩업, 미지 상태는 raw status 로 폴백(조용한 오표시 금지). */
function statusLabel(status: string): string {
  const key = `business_verification.status.${status}`;
  const label = t(key);
  return label === key ? status : label;
}

/**
 * 온보딩 wizard 1단계 — 사업자번호 상태조회/진위확인(설계 §UI 1, #248 dry/graceful).
 *
 * 정직·보안(§2, 설계 §비기능):
 * - 입력한 원문 번호는 로그/캐시/쿼리키/스토어에 남기지 않고 mutation 본문으로만 전송한다.
 * - 확인 결과는 응답의 `masked_number` 로만 표시하고 원문을 확정값처럼 재노출하지 않는다.
 * - 이 단계는 프로필을 자동 채우지 않는다 — 상태 확인 게이트일 뿐(설계 §32).
 * - inactive/mismatch 는 진행 전 명확한 warning, unknown 은 진행을 막지 않는 정직 고지.
 */
export function BusinessStep({ session, onProceed, onSkip }: BusinessStepProps) {
  const meta = STEP_META.business;
  const mutation = useVerifyBusinessNumberMutation(session);
  const {
    register,
    handleSubmit,
    formState: { errors }
  } = useForm<BusinessVerificationFormValues>({
    resolver: zodResolver(businessVerificationSchema),
    defaultValues: EMPTY_DEFAULTS
  });

  const submit = handleSubmit((values) => {
    // 숫자만 추려 전송(백엔드 정규화 계약). 개업일자+대표자명 둘 다 있을 때만 진위확인.
    const businessNumber = values.business_number.replace(/\D/g, "");
    const startDate = values.start_date?.replace(/\D/g, "") || null;
    const representative = values.representative_name?.trim() || null;
    const validate = Boolean(startDate && representative);
    mutation.mutate({
      business_number: businessNumber,
      start_date: validate ? startDate : null,
      representative_name: validate ? representative : null
    });
  });

  const result = mutation.data ?? null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>{meta.title}</CardTitle>
        <CardDescription>{meta.description}</CardDescription>
      </CardHeader>
      <CardContent>
        <form className="flex flex-col gap-4" onSubmit={submit} noValidate>
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-[var(--color-muted)]">
              {t("business_verification.label_business_number")}
            </span>
            <input
              className="h-9 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-3 text-sm tabular-nums"
              inputMode="numeric"
              autoComplete="off"
              placeholder={t("business_verification.placeholder_business_number")}
              aria-describedby={
                errors.business_number ? "business-number-error" : undefined
              }
              {...register("business_number")}
            />
            {errors.business_number ? (
              <span id="business-number-error" className="text-[11px] text-[var(--color-danger)]">
                {errors.business_number.message}
              </span>
            ) : null}
          </label>

          <div className="grid grid-cols-2 gap-3">
            <label className="flex flex-col gap-1">
              <span className="text-xs font-medium text-[var(--color-muted)]">
                {t("business_verification.label_start_date")}
              </span>
              <input
                className="h-9 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-3 text-sm"
                autoComplete="off"
                placeholder={t("business_verification.placeholder_start_date")}
                {...register("start_date")}
              />
              {errors.start_date ? (
                <span className="text-[11px] text-[var(--color-danger)]">
                  {errors.start_date.message}
                </span>
              ) : null}
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-xs font-medium text-[var(--color-muted)]">
                {t("business_verification.label_representative_name")}
              </span>
              <input
                className="h-9 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-3 text-sm"
                autoComplete="off"
                placeholder={t("business_verification.placeholder_representative_name")}
                {...register("representative_name")}
              />
            </label>
          </div>

          <p className="text-[11px] text-[var(--color-muted)]">
            {t("business_verification.input_hint")} {t("business_verification.hint_validate")}
          </p>

          <Button type="submit" className="self-start" disabled={mutation.isPending}>
            <ShieldCheck size={15} aria-hidden="true" />
            {mutation.isPending
              ? t("business_verification.button_verify_loading")
              : t("business_verification.button_verify")}
          </Button>
        </form>

        {mutation.isError ? (
          <p role="alert" className="mt-4 text-sm text-[var(--color-danger)]">
            {mutation.error.message}
          </p>
        ) : null}

        {result ? <VerificationResult result={result} /> : null}

        <div className="mt-5 flex flex-wrap items-center gap-2 border-t border-[var(--color-border)] pt-4">
          <Button type="button" onClick={onProceed}>
            <ArrowRight size={15} aria-hidden="true" />
            {t("business_verification.button_proceed")}
          </Button>
          <Button type="button" variant="ghost" onClick={onSkip}>
            {t("business_verification.button_skip")}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

/** 확인 결과 표시 — 응답의 masked 번호/상태만 노출한다(원문 재노출 금지, §2). */
function VerificationResult({ result }: { result: BusinessVerificationResponse }) {
  const statusMeta = businessStatusMeta(result.status);
  return (
    <div
      role="status"
      aria-label={t("business_verification.result_region_label")}
      className="mt-4 flex flex-col gap-3 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] p-3"
    >
      <dl className="flex flex-col gap-2 text-sm">
        <div className="flex items-center justify-between gap-2">
          <dt className="text-xs text-[var(--color-muted)]">
            {t("business_verification.result_status_label")}
          </dt>
          <dd>
            <Badge tone={statusMeta.tone}>{statusLabel(result.status)}</Badge>
          </dd>
        </div>
        <div className="flex items-center justify-between gap-2">
          <dt className="text-xs text-[var(--color-muted)]">
            {t("business_verification.result_masked_label")}
          </dt>
          <dd className="tabular-nums">{result.masked_number}</dd>
        </div>
        <div className="flex items-center justify-between gap-2">
          <dt className="text-xs text-[var(--color-muted)]">
            {t("business_verification.result_verified_at_label")}
          </dt>
          <dd className="tabular-nums">{formatDateTime(result.verified_at)}</dd>
        </div>
        {result.detail ? (
          <p className="text-xs text-[var(--color-muted)]">{result.detail}</p>
        ) : null}
      </dl>

      {statusMeta.kind === "success" ? (
        <p className="text-xs text-[color-mix(in_oklch,var(--color-success),black_20%)]">
          {t("business_verification.success_note")}
        </p>
      ) : null}

      {statusMeta.kind === "unknown" ? (
        <p className="flex items-start gap-1.5 text-xs text-[var(--color-muted)]">
          <Info size={14} aria-hidden="true" className="mt-0.5 shrink-0" />
          {t("business_verification.unknown_note")}
        </p>
      ) : null}

      {statusMeta.kind === "warning" ? (
        <div
          role="alert"
          className="flex items-start gap-2 rounded-md border border-[var(--color-danger)] bg-[color-mix(in_oklch,var(--color-danger),white_82%)] p-2.5 text-xs text-[color-mix(in_oklch,var(--color-danger),black_25%)]"
        >
          <AlertTriangle size={15} aria-hidden="true" className="mt-0.5 shrink-0" />
          <span className="flex flex-col gap-0.5">
            <strong className="font-semibold">
              {t("business_verification.warning_title")}
            </strong>
            <span>{t("business_verification.warning_body")}</span>
          </span>
        </div>
      ) : null}
    </div>
  );
}
