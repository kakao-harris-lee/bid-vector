import { Controller, type Control, type FieldErrors, type UseFormRegister } from "react-hook-form";
import { Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import { NumberField, ThresholdControl } from "@/shared/components";
import { formatPercent } from "@/shared/lib";
import type { StrategyFormValues } from "../schema";

export function StrategyThresholdSection({
  control,
  register,
  errors
}: {
  control: Control<StrategyFormValues>;
  register: UseFormRegister<StrategyFormValues>;
  errors: FieldErrors<StrategyFormValues>;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>임계값</CardTitle>
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Controller
          control={control}
          name="minimum_match_score"
          render={({ field }) => (
            <ThresholdControl
              label="최소 매칭 점수"
              value={field.value}
              onChange={field.onChange}
              min={0}
              max={1}
              step={0.01}
              format={formatPercent}
              error={errors.minimum_match_score?.message}
            />
          )}
        />
        <Controller
          control={control}
          name="minimum_probability_score"
          render={({ field }) => (
            <ThresholdControl
              label="최소 가격 적합도"
              value={field.value}
              onChange={field.onChange}
              min={0}
              max={1}
              step={0.01}
              format={formatPercent}
              error={errors.minimum_probability_score?.message}
            />
          )}
        />
        <Controller
          control={control}
          name="bid_now_threshold"
          render={({ field }) => (
            <ThresholdControl
              label="즉시 투찰 임계값"
              value={field.value}
              onChange={field.onChange}
              min={0}
              max={1}
              step={0.01}
              format={formatPercent}
              description="이 값 이상이면 'bid_now'로 분류"
              error={errors.bid_now_threshold?.message}
            />
          )}
        />
        <Controller
          control={control}
          name="review_threshold"
          render={({ field }) => (
            <ThresholdControl
              label="검토 임계값"
              value={field.value}
              onChange={field.onChange}
              min={0}
              max={1}
              step={0.01}
              format={formatPercent}
              description="즉시 투찰 임계값보다 크면 안 됩니다."
              error={errors.review_threshold?.message}
            />
          )}
        />
        <Controller
          control={control}
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
          register={register("max_recommended_candidates", { valueAsNumber: true })}
          error={errors.max_recommended_candidates?.message}
          min={1}
          max={100}
          step={1}
        />
      </CardContent>
    </Card>
  );
}
