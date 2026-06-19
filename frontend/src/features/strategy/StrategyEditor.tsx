import { useEffect, useMemo } from "react";
import { Controller, useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { Button, Card, CardContent, CardHeader, CardTitle, toastApi } from "@/shared/components/ui";
import { ChipInput, ThresholdControl } from "@/shared/components";
import { useShellContext } from "@/app/dashboardContext";
import { ApiError } from "@/shared/api";
import { logoutSession } from "@/app/layout/AuthGate";
import { EmptyState, LoadingState } from "@/features/dashboard/components";
import { CandidatesPreview } from "./CandidatesPreview";
import { RecentRuns } from "./RecentRuns";
import { useStrategyQuery, useUpdateStrategyMutation } from "./hooks";
import { strategyFormSchema, type StrategyFormValues } from "./schema";

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
          <div
            role="note"
            data-testid="strategy-readonly-notice"
            className="rounded-md border border-[var(--color-warn)] bg-[color-mix(in_oklch,var(--color-warn),white_88%)] px-3 py-2 text-xs"
          >
            현재 회사: {currentOperatorLabel ?? "다른 회사"} · 편집은 본인 회사로 돌아가야
            가능합니다.
          </div>
        ) : null}
        <fieldset disabled={readOnly} className="contents">
        <Card>
          <CardHeader>
            <CardTitle>대상</CardTitle>
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
                  placeholder="용역, 물품 ..."
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

        <Card>
          <CardHeader>
            <CardTitle>예산 범위</CardTitle>
          </CardHeader>
          <CardContent className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <NumberField
              label="최소 예산 (원)"
              register={form.register("min_budget_estimate", { valueAsNumber: true })}
              error={errors.min_budget_estimate?.message}
              step={1_000_000}
            />
            <NumberField
              label="최대 예산 (원, 0=무제한)"
              register={form.register("max_budget_estimate", { valueAsNumber: true })}
              error={errors.max_budget_estimate?.message}
              step={1_000_000}
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>임계값</CardTitle>
          </CardHeader>
          <CardContent className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Controller
              control={form.control}
              name="minimum_match_score"
              render={({ field }) => (
                <ThresholdControl
                  label="최소 매칭 점수"
                  value={field.value}
                  onChange={field.onChange}
                  min={0}
                  max={1}
                  step={0.01}
                  format={formatRatio}
                  error={errors.minimum_match_score?.message}
                />
              )}
            />
            <Controller
              control={form.control}
              name="minimum_probability_score"
              render={({ field }) => (
                <ThresholdControl
                  label="최소 가격 적합도"
                  value={field.value}
                  onChange={field.onChange}
                  min={0}
                  max={1}
                  step={0.01}
                  format={formatRatio}
                  error={errors.minimum_probability_score?.message}
                />
              )}
            />
            <Controller
              control={form.control}
              name="bid_now_threshold"
              render={({ field }) => (
                <ThresholdControl
                  label="즉시 투찰 임계값"
                  value={field.value}
                  onChange={field.onChange}
                  min={0}
                  max={1}
                  step={0.01}
                  format={formatRatio}
                  description="이 값 이상이면 'bid_now'로 분류"
                  error={errors.bid_now_threshold?.message}
                />
              )}
            />
            <Controller
              control={form.control}
              name="review_threshold"
              render={({ field }) => (
                <ThresholdControl
                  label="검토 임계값"
                  value={field.value}
                  onChange={field.onChange}
                  min={0}
                  max={1}
                  step={0.01}
                  format={formatRatio}
                  description="즉시 투찰 임계값보다 크면 안 됩니다."
                  error={errors.review_threshold?.message}
                />
              )}
            />
            <Controller
              control={form.control}
              name="auto_workload_penalty_multiplier"
              render={({ field }) => (
                <ThresholdControl
                  label="자동 워크로드 패널티 배수"
                  value={field.value}
                  onChange={field.onChange}
                  min={0}
                  max={2}
                  step={0.05}
                  description="활성 입찰 수에 따른 우선순위 감점 배수 (0~2)."
                  error={errors.auto_workload_penalty_multiplier?.message}
                />
              )}
            />
            <NumberField
              label="최대 추천 후보 수"
              register={form.register("max_recommended_candidates", { valueAsNumber: true })}
              error={errors.max_recommended_candidates?.message}
              min={1}
              max={100}
              step={1}
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>알림</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            <Controller
              control={form.control}
              name="notify_only_high_priority"
              render={({ field }) => (
                <label className="flex cursor-pointer items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={field.value}
                    onChange={(event) => field.onChange(event.target.checked)}
                    className="h-4 w-4 accent-[var(--color-primary)]"
                  />
                  <span>높은 우선순위 후보에만 알림 보내기</span>
                </label>
              )}
            />
          </CardContent>
        </Card>

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

function formatRatio(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
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
  register: ReturnType<ReturnType<typeof useForm<StrategyFormValues>>["register"]>;
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
