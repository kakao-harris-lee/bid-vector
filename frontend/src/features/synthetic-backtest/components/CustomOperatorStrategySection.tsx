import type { FieldErrors, UseFormRegister } from "react-hook-form";
import { Input } from "@/shared/components/ui";
import type { FormValues } from "../customOperatorForm.schema";

export function CustomOperatorStrategySection({
  register,
  errors
}: {
  register: UseFormRegister<FormValues>;
  errors: FieldErrors<FormValues>;
}) {
  return (
    <fieldset className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      <legend className="mb-1 text-xs font-medium text-[var(--color-muted)]">전략 파라미터</legend>

      <label className="flex flex-col gap-1 text-xs">
        <span className="text-[var(--color-muted)]">관심 카테고리 (콤마 구분)</span>
        <Input aria-label="관심 카테고리" {...register("focus_categories")} />
      </label>

      <label className="flex flex-col gap-1 text-xs">
        <span className="text-[var(--color-muted)]">관심 지역 (콤마 구분)</span>
        <Input aria-label="관심 지역" {...register("focus_regions")} />
      </label>

      <label className="flex flex-col gap-1 text-xs">
        <span className="text-[var(--color-muted)]">제외 지역 (콤마 구분)</span>
        <Input aria-label="제외 지역" {...register("exclude_regions")} />
      </label>

      <label className="flex flex-col gap-1 text-xs">
        <span className="text-[var(--color-muted)]">필수 키워드 (콤마 구분)</span>
        <Input aria-label="필수 키워드" {...register("required_keywords")} />
      </label>

      <label className="flex flex-col gap-1 text-xs">
        <span className="text-[var(--color-muted)]">제외 키워드 (콤마 구분)</span>
        <Input aria-label="제외 키워드" {...register("exclude_keywords")} />
      </label>

      <label className="flex flex-col gap-1 text-xs">
        <span className="text-[var(--color-muted)]">최소 추정예산 (원)</span>
        <Input
          type="number"
          min={0}
          aria-label="최소 추정예산"
          className="tabular-nums"
          {...register("min_budget_estimate")}
        />
        {errors.min_budget_estimate ? (
          <span className="text-[var(--color-danger)]">
            {errors.min_budget_estimate.message}
          </span>
        ) : null}
      </label>

      <label className="flex flex-col gap-1 text-xs">
        <span className="text-[var(--color-muted)]">최대 추정예산 (원)</span>
        <Input
          type="number"
          min={0}
          aria-label="최대 추정예산"
          className="tabular-nums"
          {...register("max_budget_estimate")}
        />
        {errors.max_budget_estimate ? (
          <span className="text-[var(--color-danger)]">
            {errors.max_budget_estimate.message}
          </span>
        ) : null}
      </label>

      <label className="flex flex-col gap-1 text-xs">
        <span className="text-[var(--color-muted)]">minimum_match_score (0~1)</span>
        <Input
          type="number"
          step="0.01"
          min={0}
          max={1}
          aria-label="minimum_match_score"
          className="tabular-nums"
          {...register("minimum_match_score")}
        />
        {errors.minimum_match_score ? (
          <span className="text-[var(--color-danger)]">
            {errors.minimum_match_score.message}
          </span>
        ) : null}
      </label>

      <label className="flex flex-col gap-1 text-xs">
        <span className="text-[var(--color-muted)]">minimum_probability_score (0~1)</span>
        <Input
          type="number"
          step="0.01"
          min={0}
          max={1}
          aria-label="minimum_probability_score"
          className="tabular-nums"
          {...register("minimum_probability_score")}
        />
        {errors.minimum_probability_score ? (
          <span className="text-[var(--color-danger)]">
            {errors.minimum_probability_score.message}
          </span>
        ) : null}
      </label>

      <label className="flex flex-col gap-1 text-xs">
        <span className="text-[var(--color-muted)]">bid_now_threshold (0~1)</span>
        <Input
          type="number"
          step="0.01"
          min={0}
          max={1}
          aria-label="bid_now_threshold"
          className="tabular-nums"
          {...register("bid_now_threshold")}
        />
        {errors.bid_now_threshold ? (
          <span className="text-[var(--color-danger)]">
            {errors.bid_now_threshold.message}
          </span>
        ) : null}
      </label>

      <label className="flex flex-col gap-1 text-xs">
        <span className="text-[var(--color-muted)]">review_threshold (0~1)</span>
        <Input
          type="number"
          step="0.01"
          min={0}
          max={1}
          aria-label="review_threshold"
          className="tabular-nums"
          {...register("review_threshold")}
        />
        {errors.review_threshold ? (
          <span className="text-[var(--color-danger)]">
            {errors.review_threshold.message}
          </span>
        ) : null}
      </label>

      <label className="flex flex-col gap-1 text-xs">
        <span className="text-[var(--color-muted)]">max_recommended_candidates</span>
        <Input
          type="number"
          min={0}
          aria-label="max_recommended_candidates"
          className="tabular-nums"
          {...register("max_recommended_candidates")}
        />
        {errors.max_recommended_candidates ? (
          <span className="text-[var(--color-danger)]">
            {errors.max_recommended_candidates.message}
          </span>
        ) : null}
      </label>
    </fieldset>
  );
}
