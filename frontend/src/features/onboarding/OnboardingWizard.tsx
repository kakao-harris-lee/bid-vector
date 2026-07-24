import { useState, type ReactElement } from "react";
import { useNavigate } from "react-router-dom";
import { History } from "lucide-react";
import { useShellContext } from "@/app/dashboardContext";
import { ONBOARDING_HISTORY_ROUTE_PATH } from "@/app/layout/Shell";
import { Button } from "@/shared/components/ui";
import type { OnboardingApplyResponse, OnboardingSuggestionsQuery } from "@/shared/api";
import type { OnboardingSeedFormValues } from "./schema";
import { STEP_META, type StepKey } from "./constants";
import { buildApplyDecisions, type DecisionMap, type DecisionState } from "./decisions";
import { useApplyOnboardingMutation, useOnboardingSuggestions } from "./hooks";
import { WizardProgress } from "./WizardProgress";
import { BusinessStep } from "./BusinessStep";
import { SeedStep } from "./SeedStep";
import { ReviewStep } from "./ReviewStep";
import { ResultStep } from "./ResultStep";
import { PreviewStep } from "./PreviewStep";

/**
 * 사업자번호 기반 반자동 온보딩 wizard(설계 §UI 1~5단계).
 *
 * 진입점은 1단계 "사업자번호 확인"(#248, dry/graceful — 서비스키 없으면 unknown 이라도
 * 진행 가능)이다. 사업자번호만으로 프로필을 자동 확정하지 않고 상태만 확인한 뒤 seed 로
 * 넘어간다(설계 §32). 상태 전이는 STEP 룩업(§4.5-2)으로 흐르고, 확정하지 않은(pending)
 * 후보는 절대 전송하지 않는다(§2 정직 명세). 확정(accepted)·수정(modified)·거부
 * (rejected) 결정은 감사에 전송하되, 프로필/전략 반영은 백엔드가 accepted/modified 만
 * 강제한다(rejected는 감사 전용).
 */
export function OnboardingWizard() {
  const { session } = useShellContext();
  const navigate = useNavigate();
  const [step, setStep] = useState<StepKey>("business");
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
    business: () => (
      <BusinessStep
        session={session}
        onProceed={() => setStep("seed")}
        onSkip={() => setStep("seed")}
      />
    ),
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
      <header className="flex items-start justify-between gap-2">
        <div className="flex flex-col gap-1">
          <h1 className="text-lg font-semibold">사업자 온보딩</h1>
          <p className="text-sm text-[var(--color-muted)]">
            {STEP_META[effectiveStep].description}
          </p>
        </div>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="shrink-0"
          onClick={() => navigate(ONBOARDING_HISTORY_ROUTE_PATH)}
        >
          <History size={15} aria-hidden="true" /> 감사 이력
        </Button>
      </header>
      <WizardProgress current={effectiveStep} />
      {stepViews[effectiveStep]()}
    </section>
  );
}
