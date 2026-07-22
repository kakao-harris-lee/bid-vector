import { useState, type ReactElement } from "react";
import { useShellContext } from "@/app/dashboardContext";
import type { OnboardingApplyResponse, OnboardingSuggestionsQuery } from "@/shared/api";
import type { OnboardingSeedFormValues } from "./schema";
import { STEP_META, type StepKey } from "./constants";
import { buildApplyDecisions, type DecisionMap, type DecisionState } from "./decisions";
import { useApplyOnboardingMutation, useOnboardingSuggestions } from "./hooks";
import { WizardProgress } from "./WizardProgress";
import { SeedStep } from "./SeedStep";
import { ReviewStep } from "./ReviewStep";
import { ResultStep } from "./ResultStep";
import { PreviewStep } from "./PreviewStep";

/**
 * 사업자번호 기반 반자동 온보딩 wizard(설계 §UI 2~5단계).
 *
 * 1단계(사업자번호·국세청 상태확인)는 국세청 미구현이라 후속으로 미룬다. 진입점은
 * "기본 후보"(키워드 seed)다. 상태 전이는 STEP 룩업(§4.5-2)으로 흐르고, 확정하지
 * 않은 후보는 절대 전송하지 않는다(§2 정직 명세, apply=accepted-only).
 */
export function OnboardingWizard() {
  const { session } = useShellContext();
  const [step, setStep] = useState<StepKey>("seed");
  const [seed, setSeed] = useState<OnboardingSuggestionsQuery | null>(null);
  const [overrides, setOverrides] = useState<DecisionMap>({});
  const [applyResult, setApplyResult] = useState<OnboardingApplyResponse | null>(null);

  const suggestionsQuery = useOnboardingSuggestions(session, seed, {
    enabled: seed !== null
  });
  const applyMutation = useApplyOnboardingMutation(session);

  const handleSeedSubmit = (query: OnboardingSuggestionsQuery) => {
    setSeed(query);
    setOverrides({});
    setApplyResult(null);
    applyMutation.reset();
    setStep("review");
  };

  const handleDecision = (field: string, next: DecisionState) => {
    setOverrides((prev) => ({ ...prev, [field]: next }));
  };

  const handleApply = () => {
    const data = suggestionsQuery.data;
    if (!data) return;
    const decisions = buildApplyDecisions([...(data.profile ?? []), ...(data.strategy ?? [])], overrides);
    applyMutation.mutate(
      { decisions },
      {
        onSuccess: (result) => {
          setApplyResult(result);
          setStep("result");
        }
      }
    );
  };

  const handleRestart = () => {
    setSeed(null);
    setOverrides({});
    setApplyResult(null);
    applyMutation.reset();
    setStep("seed");
  };

  // result 단계는 apply 성공 이후에만 유효 — 방어적으로 review 로 되돌린다.
  const effectiveStep: StepKey = step === "result" && !applyResult ? "review" : step;

  const seedDefaults: OnboardingSeedFormValues | undefined = seed
    ? {
        keywords: seed.keywords,
        region: seed.region ?? "",
        min_budget: seed.minBudget ?? null,
        max_budget: seed.maxBudget ?? null
      }
    : undefined;

  const stepViews: Record<StepKey, () => ReactElement> = {
    seed: () => <SeedStep defaultValues={seedDefaults} onSubmit={handleSeedSubmit} />,
    review: () => (
      <ReviewStep
        query={suggestionsQuery}
        overrides={overrides}
        onDecision={handleDecision}
        onApply={handleApply}
        onBack={() => setStep("seed")}
        applyPending={applyMutation.isPending}
        applyError={applyMutation.error?.message ?? null}
      />
    ),
    result: () =>
      applyResult ? (
        <ResultStep result={applyResult} onContinue={() => setStep("preview")} />
      ) : (
        stepViews.review()
      ),
    preview: () => <PreviewStep onRestart={handleRestart} />
  };

  return (
    <section className="flex flex-col gap-4" aria-label="온보딩 마법사">
      <header className="flex flex-col gap-1">
        <h1 className="text-lg font-semibold">사업자 온보딩</h1>
        <p className="text-sm text-[var(--color-muted)]">{STEP_META[effectiveStep].description}</p>
      </header>
      <WizardProgress current={effectiveStep} />
      {stepViews[effectiveStep]()}
    </section>
  );
}
