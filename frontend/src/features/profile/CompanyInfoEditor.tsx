import { useEffect, useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { toastApi } from "@/shared/components/ui";
import { ReadOnlyContextNotice } from "@/shared/components";
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
  BasicsCard,
  BudgetCard,
  CapacityCard,
  EditorActions,
  EditorHeader,
  PilotReadinessCard,
  PreferencesCard,
  WizardProgress,
  WIZARD_STEPS,
  defaultValues,
  settle,
  toFormValues,
  type EditorMode
} from "./components";

export function CompanyInfoEditor() {
  const { session, activeOperator } = useShellContext();
  // GET supports cross-operator reads on PR #74 — pass the active operator so
  // the screen mirrors whichever company the dashboard is scoped to. `null`
  // (token owner) keeps the URL bare. Edits stay self-only on the backend, so
  // impersonation views are forced into read-only mode here.
  const activeOperatorId = activeOperator.activeOperatorId;
  const isOwnContext = activeOperatorId === null;
  const currentOperatorLabel = activeOperator.currentOperator
    ? activeOperator.currentOperator.company ||
      activeOperator.currentOperator.full_name ||
      activeOperator.currentOperator.username
    : null;

  const profileQuery = useProfileQuery(session, activeOperatorId);
  const strategyQuery = useProfileStrategyQuery(session, activeOperatorId);
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
  // For impersonation contexts we never auto-enter the wizard — the editor
  // stays read-only since PUT is self-only on the backend (PR #74).
  const [mode, setMode] = useState<EditorMode | null>(null);
  const [step, setStep] = useState<number>(0);

  useEffect(() => {
    if (mode !== null) return;
    if (!profileQuery.data) return;
    if (!isOwnContext) {
      setMode("single");
      return;
    }
    setMode(profileQuery.data.profile_configured ? "single" : "wizard");
  }, [mode, profileQuery.data, isOwnContext]);

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
        tech_fields: values.tech_fields,
        association_memberships: values.association_memberships,
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

    if (errors.some((err) => err instanceof ApiError && err.status === 403)) {
      toastApi.danger({
        title: "다른 회사 프로필은 편집할 수 없습니다.",
        description: "본인 회사 컨텍스트로 돌아간 뒤 다시 시도하세요."
      });
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
  const readOnly = !isOwnContext;
  // Impersonation views collapse to single-page read-only; the wizard CTA is
  // pointless because we cannot persist edits for other companies.
  const resolvedMode: EditorMode = readOnly ? "single" : mode ?? "single";
  const isWizard = resolvedMode === "wizard";
  const activeStep = WIZARD_STEPS[Math.min(step, WIZARD_STEPS.length - 1)];
  const isLastStep = step >= WIZARD_STEPS.length - 1;
  const stepCount = WIZARD_STEPS.length;
  const readinessValues = form.watch();

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
        <EditorHeader
          readOnly={readOnly}
          isWizard={isWizard}
          step={step}
          stepCount={stepCount}
          activeStepTitle={activeStep.title}
          onToggle={toggleMode}
        />

        {readOnly ? (
          <ReadOnlyContextNotice
            operatorLabel={currentOperatorLabel}
            testId="profile-readonly-notice"
          />
        ) : null}

        {isWizard ? (
          <WizardProgress current={step} total={stepCount} steps={WIZARD_STEPS} />
        ) : null}

        <fieldset disabled={readOnly} className="contents">
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
        </fieldset>

        {readOnly ? null : (
          <EditorActions
            isWizard={isWizard}
            step={step}
            saving={saving}
            isLastStep={isLastStep}
            onPrev={goPrev}
            onNext={goNext}
            onReset={() => form.reset(formInitial)}
            resetDisabled={saving || !form.formState.isDirty}
          />
        )}
      </form>

      <aside className="flex flex-col gap-4">
        <PilotReadinessCard values={readinessValues} />
        <BacktestPanel />
      </aside>
    </div>
  );
}
