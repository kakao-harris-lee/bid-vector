import { useEffect, useMemo, useState } from "react";
import { Controller, useForm, type UseFormReturn } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { Button, Card, CardContent, CardHeader, CardTitle, toastApi } from "@/shared/components/ui";
import { ChipInput, ThresholdControl } from "@/shared/components";
import { useShellContext } from "@/app/dashboardContext";
import { ApiError } from "@/shared/api";
import { logoutSession } from "@/app/layout/AuthGate";
import { EmptyState, LoadingState } from "@/features/dashboard/components";
import { BacktestPanel } from "./BacktestPanel";
import {
  useProfileQuery,
  useProfileStrategyQuery,
  useUpdateProfileMutation,
  useUpdateProfileStrategyMutation
} from "./hooks";
import { companyInfoFormSchema, type CompanyInfoFormValues } from "./schema";
import {
  BUSINESS_TYPE_OPTIONS,
  LICENSE_CHIPS,
  RECORD_ONLY_LICENSE_CHIPS,
  REGION_CHIPS,
  type LicenseChip
} from "./constants";
import type { OperatorProfileResponse } from "@/shared/types/profile";
import type { OperatorStrategyResponse } from "@/shared/types/strategy";

type EditorMode = "wizard" | "single";

interface WizardStep {
  key: "basics" | "capacity" | "budget" | "preferences";
  title: string;
  description: string;
}

const WIZARD_STEPS: readonly WizardStep[] = [
  {
    key: "basics",
    title: "기본 · 면허 · 지역",
    description: "업무 구분과 보유 면허, 수행 지역을 골라 매칭 기준을 만듭니다."
  },
  {
    key: "capacity",
    title: "공사 능력 · 실적",
    description: "시공능력평가액·도급한도는 공사 매칭 정확도를 크게 좌우합니다."
  },
  {
    key: "budget",
    title: "공사 가능 금액",
    description: "참여 가능한 예산 범위를 입력하세요. 빈 값은 무제한으로 간주합니다."
  },
  {
    key: "preferences",
    title: "선호 · 제외 조건",
    description: "중점 카테고리·지역과 필수/제외 키워드를 더해 후보 폭을 좁힙니다."
  }
] as const;

const defaultValues: CompanyInfoFormValues = {
  business_type: BUSINESS_TYPE_OPTIONS[0].value,
  license_codes: [],
  region_codes: [],
  annual_revenue: 0,
  construction_capacity_amount: 0,
  awarded_contract_limit: 0,
  total_awards: 0,
  capacity_score: 0.5,
  min_budget_estimate: 0,
  max_budget_estimate: 0,
  focus_categories: [],
  focus_regions: [],
  exclude_regions: [],
  required_keywords: [],
  exclude_keywords: []
};

export function CompanyInfoEditor() {
  const { session } = useShellContext();
  const profileQuery = useProfileQuery(session);
  const strategyQuery = useProfileStrategyQuery(session);
  const profileMutation = useUpdateProfileMutation(session);
  const strategyMutation = useUpdateProfileStrategyMutation(session);

  const formInitial = useMemo<CompanyInfoFormValues>(() => {
    if (!profileQuery.data || !strategyQuery.data) return defaultValues;
    return toFormValues(profileQuery.data, strategyQuery.data);
  }, [profileQuery.data, strategyQuery.data]);

  const form = useForm<CompanyInfoFormValues>({
    resolver: zodResolver(companyInfoFormSchema),
    defaultValues: formInitial,
    mode: "onSubmit"
  });

  // Mode is decided once the first profile payload arrives:
  //   profile_configured === false → guided 4-step wizard
  //   profile_configured === true  → single-page edit (existing behaviour)
  // The user can also toggle manually after the initial decision.
  const [mode, setMode] = useState<EditorMode | null>(null);
  const [step, setStep] = useState<number>(0);

  useEffect(() => {
    if (mode !== null) return;
    if (!profileQuery.data) return;
    setMode(profileQuery.data.profile_configured ? "single" : "wizard");
  }, [mode, profileQuery.data]);

  useEffect(() => {
    if (profileQuery.data && strategyQuery.data) {
      form.reset(toFormValues(profileQuery.data, strategyQuery.data));
    }
  }, [profileQuery.data, strategyQuery.data, form]);

  const submit = form.handleSubmit(async (values) => {
    // The two updates are independent owners: profile fields go to the
    // operator profile, the budget/preference fields are a *partial* strategy
    // update so the thresholds owned by StrategyEditor are preserved.
    const profileResult = await settle(() =>
      profileMutation.mutateAsync({
        business_type: values.business_type,
        license_codes: values.license_codes,
        region_codes: values.region_codes,
        annual_revenue: values.annual_revenue,
        capacity_score: values.capacity_score,
        construction_capacity_amount: values.construction_capacity_amount,
        awarded_contract_limit: values.awarded_contract_limit,
        total_awards: values.total_awards
      })
    );
    const strategyResult = await settle(() =>
      strategyMutation.mutateAsync({
        min_budget_estimate: values.min_budget_estimate,
        max_budget_estimate: values.max_budget_estimate,
        focus_categories: values.focus_categories,
        focus_regions: values.focus_regions,
        exclude_regions: values.exclude_regions,
        required_keywords: values.required_keywords,
        exclude_keywords: values.exclude_keywords
      })
    );

    const errors = [profileResult.error, strategyResult.error].filter(
      (err): err is Error => err != null
    );

    if (errors.some((err) => err instanceof ApiError && err.status === 401)) {
      logoutSession();
      return;
    }

    if (errors.length === 0) {
      toastApi.success({
        title: "업체 정보 저장 완료",
        description: "면허·지역·예산이 매칭에 반영됩니다."
      });
      // First-time入력 마법사 → single 편집으로 전환.
      if (mode === "wizard") {
        setMode("single");
        setStep(0);
      }
      return;
    }

    const failedAreas: string[] = [];
    if (profileResult.error) failedAreas.push("기본·면허·지역·실적");
    if (strategyResult.error) failedAreas.push("금액·선호 조건");
    toastApi.danger({
      title: "업체 정보 저장 실패",
      description: `${failedAreas.join(", ")} 저장에 실패했습니다: ${errors[0].message}`
    });
  });

  const isPending = profileQuery.isPending || strategyQuery.isPending;
  const hasData = profileQuery.data && strategyQuery.data;

  if (isPending && !hasData) return <LoadingState />;
  if ((profileQuery.error || strategyQuery.error) && !hasData) {
    const err = profileQuery.error ?? strategyQuery.error;
    return (
      <EmptyState
        title="업체 정보를 불러오지 못했습니다"
        detail={err?.message ?? "API 호출에 실패했습니다."}
      />
    );
  }

  const errors = form.formState.errors;
  const saving = profileMutation.isPending || strategyMutation.isPending;
  const resolvedMode: EditorMode = mode ?? "single";
  const isWizard = resolvedMode === "wizard";
  const activeStep = WIZARD_STEPS[Math.min(step, WIZARD_STEPS.length - 1)];
  const isLastStep = step >= WIZARD_STEPS.length - 1;
  const stepCount = WIZARD_STEPS.length;

  const toggleMode = () => {
    if (isWizard) {
      setMode("single");
    } else {
      setStep(0);
      setMode("wizard");
    }
  };

  const goPrev = () => {
    setStep((current) => Math.max(0, current - 1));
  };

  const goNext = () => {
    setStep((current) => Math.min(stepCount - 1, current + 1));
  };

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_320px]">
      <form onSubmit={submit} className="flex flex-col gap-4" aria-label="업체 정보 편집">
        <header className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="text-lg font-semibold text-[var(--color-fg)]">업체 정보</h2>
          <div className="flex items-center gap-3">
            <span className="text-xs text-[var(--color-muted)]">
              {isWizard
                ? `단계 ${step + 1} / ${stepCount} — ${activeStep.title}`
                : "정확한 5축 입력이 추천 품질을 좌우합니다."}
            </span>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={toggleMode}
              aria-pressed={isWizard}
            >
              {isWizard ? "전체 보기" : "단계별 가이드"}
            </Button>
          </div>
        </header>

        {isWizard ? (
          <WizardProgress current={step} total={stepCount} steps={WIZARD_STEPS} />
        ) : null}

        {!isWizard || activeStep.key === "basics" ? (
          <BasicsCard form={form} errors={errors} />
        ) : null}

        {!isWizard || activeStep.key === "capacity" ? (
          <CapacityCard form={form} errors={errors} />
        ) : null}

        {!isWizard || activeStep.key === "budget" ? (
          <BudgetCard form={form} errors={errors} />
        ) : null}

        {!isWizard || activeStep.key === "preferences" ? (
          <PreferencesCard form={form} />
        ) : null}

        <div className="flex flex-wrap items-center justify-end gap-2">
          {isWizard ? (
            <>
              <Button
                type="button"
                variant="outline"
                onClick={goPrev}
                disabled={step === 0 || saving}
              >
                이전
              </Button>
              {isLastStep ? (
                <Button type="submit" disabled={saving}>
                  {saving ? "저장 중" : "저장하고 마치기"}
                </Button>
              ) : (
                <Button type="button" onClick={goNext} disabled={saving}>
                  다음
                </Button>
              )}
            </>
          ) : (
            <>
              <Button
                type="button"
                variant="outline"
                onClick={() => form.reset(formInitial)}
                disabled={saving || !form.formState.isDirty}
              >
                되돌리기
              </Button>
              <Button type="submit" disabled={saving}>
                {saving ? "저장 중" : "저장"}
              </Button>
            </>
          )}
        </div>
      </form>

      <aside className="flex flex-col gap-4">
        <BacktestPanel />
      </aside>
    </div>
  );
}

// ---------- Wizard sub-components ----------

interface WizardProgressProps {
  current: number;
  total: number;
  steps: readonly WizardStep[];
}

function WizardProgress({ current, total, steps }: WizardProgressProps) {
  const active = steps[Math.min(current, steps.length - 1)];
  const pct = Math.round(((current + 1) / total) * 100);
  return (
    <div
      className="flex flex-col gap-2 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] p-3"
      aria-label="등록 마법사 진행 상태"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs font-medium text-[var(--color-fg)]">
          {active.title}
        </span>
        <span className="text-[11px] text-[var(--color-muted)] tabular-nums">
          {current + 1} / {total}
        </span>
      </div>
      <div
        className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--color-border)]"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
      >
        <div
          className="h-full bg-[var(--color-primary)] transition-[width]"
          style={{ width: `${pct}%` }}
        />
      </div>
      <p className="text-[11px] text-[var(--color-muted)]">{active.description}</p>
    </div>
  );
}

interface CardProps {
  form: UseFormReturn<CompanyInfoFormValues>;
  errors: UseFormReturn<CompanyInfoFormValues>["formState"]["errors"];
}

function BasicsCard({ form, errors }: CardProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>기본 · 면허 · 지역</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium text-[var(--color-muted)]">업무 구분</span>
          <select
            className="h-9 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-3 text-sm"
            aria-label="업무 구분"
            {...form.register("business_type")}
          >
            {BUSINESS_TYPE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          {errors.business_type ? (
            <span className="text-[11px] text-[var(--color-danger)]">
              {errors.business_type.message}
            </span>
          ) : null}
        </label>

        <Controller
          control={form.control}
          name="license_codes"
          render={({ field }) => (
            <div className="flex flex-col gap-2">
              <ChipInput
                label="보유 면허"
                value={field.value}
                onChange={field.onChange}
                placeholder="면허명 또는 코드 입력"
              />
              <LicenseSuggestions value={field.value} onChange={field.onChange} />
            </div>
          )}
        />

        <Controller
          control={form.control}
          name="region_codes"
          render={({ field }) => (
            <div className="flex flex-col gap-2">
              <ChipInput
                label="수행 지역"
                value={field.value}
                onChange={field.onChange}
                placeholder="지역명 입력"
              />
              <RegionSuggestions value={field.value} onChange={field.onChange} />
            </div>
          )}
        />
      </CardContent>
    </Card>
  );
}

function CapacityCard({ form, errors }: CardProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>공사 능력 · 실적</CardTitle>
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <NumberField
          label="연매출 (원)"
          register={form.register("annual_revenue", { valueAsNumber: true })}
          error={errors.annual_revenue?.message}
          min={0}
          step={10_000_000}
        />
        <NumberField
          label="누적 낙찰 실적 (건)"
          register={form.register("total_awards", { valueAsNumber: true })}
          error={errors.total_awards?.message}
          min={0}
          step={1}
        />
        <NumberField
          label="시공능력평가액 (원, 0=미입력)"
          register={form.register("construction_capacity_amount", {
            valueAsNumber: true
          })}
          error={errors.construction_capacity_amount?.message}
          min={0}
          step={10_000_000}
        />
        <NumberField
          label="도급한도 (원, 0=미입력)"
          register={form.register("awarded_contract_limit", { valueAsNumber: true })}
          error={errors.awarded_contract_limit?.message}
          min={0}
          step={10_000_000}
        />
        <div className="sm:col-span-2">
          <Controller
            control={form.control}
            name="capacity_score"
            render={({ field }) => (
              <ThresholdControl
                label="수행 역량 (0~1)"
                value={field.value}
                onChange={field.onChange}
                min={0}
                max={1}
                step={0.05}
                description="현재 동시 수행 가능한 역량 수준. 1에 가까울수록 여유."
                error={errors.capacity_score?.message}
              />
            )}
          />
        </div>
      </CardContent>
    </Card>
  );
}

function BudgetCard({ form, errors }: CardProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>공사 가능 금액</CardTitle>
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <NumberField
          label="최소 금액 (원)"
          register={form.register("min_budget_estimate", { valueAsNumber: true })}
          error={errors.min_budget_estimate?.message}
          min={0}
          step={1_000_000}
        />
        <NumberField
          label="최대 금액 (원, 0=무제한)"
          register={form.register("max_budget_estimate", { valueAsNumber: true })}
          error={errors.max_budget_estimate?.message}
          min={0}
          step={1_000_000}
        />
      </CardContent>
    </Card>
  );
}

function PreferencesCard({ form }: { form: UseFormReturn<CompanyInfoFormValues> }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>선호 · 제외 조건</CardTitle>
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Controller
          control={form.control}
          name="focus_categories"
          render={({ field }) => (
            <ChipInput
              label="중점 카테고리"
              value={field.value}
              onChange={field.onChange}
              placeholder="공사, 용역 ..."
            />
          )}
        />
        <Controller
          control={form.control}
          name="focus_regions"
          render={({ field }) => (
            <ChipInput
              label="중점 지역"
              value={field.value}
              onChange={field.onChange}
              placeholder="서울, 경기 ..."
            />
          )}
        />
        <Controller
          control={form.control}
          name="exclude_regions"
          render={({ field }) => (
            <ChipInput
              label="제외 지역"
              value={field.value}
              onChange={field.onChange}
              placeholder="제외할 지역"
            />
          )}
        />
        <Controller
          control={form.control}
          name="required_keywords"
          render={({ field }) => (
            <ChipInput
              label="필수 키워드"
              value={field.value}
              onChange={field.onChange}
              placeholder="포함되어야 할 단어"
            />
          )}
        />
        <Controller
          control={form.control}
          name="exclude_keywords"
          render={({ field }) => (
            <ChipInput
              label="제외 키워드"
              value={field.value}
              onChange={field.onChange}
              placeholder="제외할 단어"
            />
          )}
        />
      </CardContent>
    </Card>
  );
}

interface SettleResult<T> {
  value?: T;
  error?: Error;
}

async function settle<T>(fn: () => Promise<T>): Promise<SettleResult<T>> {
  try {
    return { value: await fn() };
  } catch (err) {
    return { error: err instanceof Error ? err : new Error(String(err)) };
  }
}

function toFormValues(
  profile: OperatorProfileResponse,
  strategy: OperatorStrategyResponse
): CompanyInfoFormValues {
  return {
    business_type: profile.business_type || BUSINESS_TYPE_OPTIONS[0].value,
    license_codes: profile.license_codes ?? [],
    region_codes: profile.region_codes ?? [],
    annual_revenue: profile.annual_revenue,
    construction_capacity_amount: profile.construction_capacity_amount,
    awarded_contract_limit: profile.awarded_contract_limit,
    total_awards: profile.total_awards,
    capacity_score: profile.capacity_score,
    min_budget_estimate: strategy.min_budget_estimate,
    max_budget_estimate: strategy.max_budget_estimate,
    focus_categories: strategy.focus_categories,
    focus_regions: strategy.focus_regions,
    exclude_regions: strategy.exclude_regions,
    required_keywords: strategy.required_keywords,
    exclude_keywords: strategy.exclude_keywords
  };
}

function addToken(value: string[], onChange: (next: string[]) => void, token: string) {
  if (value.includes(token)) return;
  onChange([...value, token]);
}

function LicenseSuggestions({
  value,
  onChange
}: {
  value: string[];
  onChange: (next: string[]) => void;
}) {
  // The classifier now recognises every curated chip (including the eight
  // construction licenses), so by default there is nothing to flag as
  // record-only. Keep the note conditional in case record-only chips are
  // reintroduced later.
  const hasRecordOnly = RECORD_ONLY_LICENSE_CHIPS.length > 0;
  return (
    <div className="flex flex-col gap-1">
      <span className="text-[11px] text-[var(--color-muted)]">
        추천 면허 (클릭하면 추가)
      </span>
      <div className="flex flex-wrap gap-1.5">
        {LICENSE_CHIPS.map((chip) => (
          <LicenseChipButton
            key={chip.value}
            chip={chip}
            active={value.includes(chip.value)}
            onClick={() => addToken(value, onChange, chip.value)}
          />
        ))}
      </div>
      <span className="text-[11px] text-[var(--color-muted)]">
        {hasRecordOnly
          ? "⚠ 표시 없는 면허만 공고 요구 면허와 자동 매칭됩니다. 그 외는 기록용입니다."
          : "추천 면허는 모두 공고 요구 면허와 자동 매칭됩니다."}
      </span>
    </div>
  );
}

function LicenseChipButton({
  chip,
  active,
  onClick
}: {
  chip: LicenseChip;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={`보유 면허에 ${chip.label} 추가`}
      aria-pressed={active}
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs transition-colors ${
        active
          ? "border-[var(--color-primary)] bg-[var(--color-primary)] text-[var(--color-primary-foreground)]"
          : "border-[var(--color-border)] bg-[var(--color-card)] text-[var(--color-fg)] hover:border-[var(--color-primary)]"
      }`}
    >
      <span>{chip.label}</span>
      {chip.matchable ? null : <span aria-hidden="true">⚠</span>}
    </button>
  );
}

function RegionSuggestions({
  value,
  onChange
}: {
  value: string[];
  onChange: (next: string[]) => void;
}) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-[11px] text-[var(--color-muted)]">
        추천 지역 (클릭하면 추가)
      </span>
      <div className="flex flex-wrap gap-1.5">
        {REGION_CHIPS.map((region) => {
          const active = value.includes(region);
          return (
            <button
              key={region}
              type="button"
              onClick={() => addToken(value, onChange, region)}
              aria-label={`수행 지역에 ${region} 추가`}
              aria-pressed={active}
              className={`rounded-full border px-2 py-0.5 text-xs transition-colors ${
                active
                  ? "border-[var(--color-primary)] bg-[var(--color-primary)] text-[var(--color-primary-foreground)]"
                  : "border-[var(--color-border)] bg-[var(--color-card)] text-[var(--color-fg)] hover:border-[var(--color-primary)]"
              }`}
            >
              {region}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function NumberField({
  label,
  register,
  error,
  min,
  max,
  step
}: {
  label: string;
  register: ReturnType<ReturnType<typeof useForm<CompanyInfoFormValues>>["register"]>;
  error?: string;
  min?: number;
  max?: number;
  step?: number;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-xs font-medium text-[var(--color-muted)]">{label}</span>
      <input
        type="number"
        min={min}
        max={max}
        step={step}
        className={`h-9 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-3 text-sm tabular-nums ${
          error ? "border-[var(--color-danger)]" : ""
        }`}
        {...register}
      />
      {error ? <span className="text-[11px] text-[var(--color-danger)]">{error}</span> : null}
    </label>
  );
}
