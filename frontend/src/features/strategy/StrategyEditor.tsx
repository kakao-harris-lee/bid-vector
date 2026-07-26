import { useEffect, useMemo } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { Button, toastApi } from "@/shared/components/ui";
import { ReadOnlyContextNotice } from "@/shared/components";
import { useShellContext } from "@/app/dashboardContext";
import { ApiError } from "@/shared/api";
import { logoutSession } from "@/app/layout/AuthGate";
import { EmptyState, LoadingState } from "@/features/dashboard/components";
import { CandidatesPreview } from "./CandidatesPreview";
import { RecentRuns } from "./RecentRuns";
import { useStrategyQuery, useUpdateStrategyMutation } from "./hooks";
import { strategyFormSchema, type StrategyFormValues } from "./schema";
import {
  StrategyBudgetSection,
  StrategyDecisionGuide,
  StrategyNotificationSection,
  StrategyTargetSection,
  StrategyThresholdSection
} from "./components";

const defaultValues: StrategyFormValues = {
  focus_categories: [],
  focus_regions: [],
  exclude_regions: [],
  required_keywords: [],
  exclude_keywords: [],
  min_budget_estimate: 0,
  max_budget_estimate: 0,
  minimum_match_score: 0.6,
  minimum_probability_score: 0.55,
  bid_now_threshold: 0.7,
  review_threshold: 0.5,
  auto_workload_penalty_multiplier: 1,
  max_recommended_candidates: 10,
  notify_only_high_priority: true
};

export function StrategyEditor() {
  const { session, activeOperator } = useShellContext();
  // PR #74: GET supports `?operator_id=` for cross-operator reads. PUT stays
  // self-only; impersonation views render the form read-only.
  const activeOperatorId = activeOperator.activeOperatorId;
  const isOwnContext = activeOperatorId === null;
  const readOnly = !isOwnContext;
  const currentOperatorLabel = activeOperator.currentOperator
    ? activeOperator.currentOperator.company ||
      activeOperator.currentOperator.full_name ||
      activeOperator.currentOperator.username
    : null;
  const query = useStrategyQuery(session, activeOperatorId);
  const mutation = useUpdateStrategyMutation(session);

  const formInitial = useMemo<StrategyFormValues>(() => {
    if (!query.data) return defaultValues;
    return toFormValues(query.data);
  }, [query.data]);

  const form = useForm<StrategyFormValues>({
    resolver: zodResolver(strategyFormSchema),
    defaultValues: formInitial,
    mode: "onSubmit"
  });

  useEffect(() => {
    if (query.data) form.reset(toFormValues(query.data));
  }, [query.data, form]);

  const submit = form.handleSubmit(async (values) => {
    try {
      const updated = await mutation.mutateAsync(values);
      toastApi.success({
        title: "전략 저장 완료",
        description: updated.strategy_configured
          ? "변경 사항이 반영되었습니다."
          : "기본 전략으로 저장되었습니다."
      });
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        logoutSession();
        return;
      }
      if (err instanceof ApiError && err.status === 403) {
        toastApi.danger({
          title: "다른 회사 전략은 편집할 수 없습니다.",
          description: "본인 회사 컨텍스트로 돌아간 뒤 다시 시도하세요."
        });
        return;
      }
      toastApi.danger({
        title: "전략 저장 실패",
        description: err instanceof Error ? err.message : "알 수 없는 오류"
      });
    }
  });

  if (query.isPending && !query.data) return <LoadingState />;
  if (query.error && !query.data) {
    return (
      <EmptyState
        title="전략을 불러오지 못했습니다"
        detail={query.error.message ?? "API 호출에 실패했습니다."}
      />
    );
  }

  const errors = form.formState.errors;
  const strategyValues = form.watch();

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_320px]">
      <form onSubmit={submit} className="flex flex-col gap-4" aria-label="운영자 전략 편집">
        <header className="flex items-baseline justify-between">
          <h2 className="text-lg font-semibold text-[var(--color-fg)]">전략 편집</h2>
          <span className="text-xs text-[var(--color-muted)]">
            {readOnly
              ? "다른 회사 컨텍스트는 읽기 전용입니다."
              : "저장 시 dry-run으로 영향 후보 수가 즉시 갱신됩니다."}
          </span>
        </header>
        {readOnly ? (
          <ReadOnlyContextNotice
            operatorLabel={currentOperatorLabel}
            testId="strategy-readonly-notice"
          />
        ) : null}
        <StrategyDecisionGuide values={strategyValues} />
        <fieldset disabled={readOnly} className="contents">
          <StrategyTargetSection control={form.control} />
          <StrategyBudgetSection register={form.register} errors={errors} />
          <StrategyThresholdSection control={form.control} register={form.register} errors={errors} />
          <StrategyNotificationSection control={form.control} />
        </fieldset>
        {readOnly ? null : (
          <div className="flex items-center justify-end gap-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => form.reset(formInitial)}
              disabled={mutation.isPending || !form.formState.isDirty}
            >
              되돌리기
            </Button>
            <Button type="submit" disabled={mutation.isPending}>
              {mutation.isPending ? "저장 중" : "저장"}
            </Button>
          </div>
        )}
      </form>

      <aside className="flex flex-col gap-4">
        <CandidatesPreview />
        <RecentRuns />
      </aside>
    </div>
  );
}

function toFormValues(strategy: NonNullable<ReturnType<typeof useStrategyQuery>["data"]>): StrategyFormValues {
  return {
    focus_categories: strategy.focus_categories,
    focus_regions: strategy.focus_regions,
    exclude_regions: strategy.exclude_regions,
    required_keywords: strategy.required_keywords,
    exclude_keywords: strategy.exclude_keywords,
    min_budget_estimate: strategy.min_budget_estimate,
    max_budget_estimate: strategy.max_budget_estimate,
    minimum_match_score: strategy.minimum_match_score,
    minimum_probability_score: strategy.minimum_probability_score,
    bid_now_threshold: strategy.bid_now_threshold,
    review_threshold: strategy.review_threshold,
    auto_workload_penalty_multiplier: strategy.auto_workload_penalty_multiplier,
    max_recommended_candidates: strategy.max_recommended_candidates,
    notify_only_high_priority: strategy.notify_only_high_priority
  };
}
