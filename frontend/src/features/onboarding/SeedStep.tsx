import { Controller, useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { Search } from "lucide-react";
import { Button, Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/shared/components/ui";
import { ChipInput } from "@/shared/components";
import type { OnboardingSuggestionsQuery } from "@/shared/api";
import { onboardingSeedSchema, type OnboardingSeedFormValues } from "./schema";
import { STEP_META } from "./constants";

export interface SeedStepProps {
  /** 이전에 확정한 seed(뒤로 왔을 때 폼 복원용). */
  defaultValues?: OnboardingSeedFormValues;
  onSubmit: (query: OnboardingSuggestionsQuery) => void;
}

const EMPTY_DEFAULTS: OnboardingSeedFormValues = {
  keywords: [],
  region: "",
  min_budget: null,
  max_budget: null
};

export function SeedStep({ defaultValues, onSubmit }: SeedStepProps) {
  const meta = STEP_META.seed;
  const {
    control,
    register,
    handleSubmit,
    formState: { errors }
  } = useForm<OnboardingSeedFormValues>({
    resolver: zodResolver(onboardingSeedSchema),
    defaultValues: defaultValues ?? EMPTY_DEFAULTS
  });

  const submit = handleSubmit((values) => {
    onSubmit({
      keywords: values.keywords,
      region: values.region?.trim() ? values.region.trim() : null,
      minBudget: typeof values.min_budget === "number" ? values.min_budget : null,
      maxBudget: typeof values.max_budget === "number" ? values.max_budget : null
    });
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>{meta.title}</CardTitle>
        <CardDescription>{meta.description}</CardDescription>
      </CardHeader>
      <CardContent>
        <form className="flex flex-col gap-4" onSubmit={submit} noValidate>
          <div className="flex flex-col gap-1">
            <Controller
              control={control}
              name="keywords"
              render={({ field }) => (
                <ChipInput
                  label="관심 키워드 (필수)"
                  value={field.value ?? []}
                  onChange={field.onChange}
                  separators={[","]}
                  placeholder="예: 항만, 준설 — 입력 후 Enter"
                  ariaDescribedBy={errors.keywords ? "seed-keywords-error" : undefined}
                />
              )}
            />
            {errors.keywords ? (
              <span id="seed-keywords-error" className="text-[11px] text-[var(--color-danger)]">
                {errors.keywords.message}
              </span>
            ) : null}
          </div>

          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-[var(--color-muted)]">지역 힌트 (선택)</span>
            <input
              className="h-9 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-3 text-sm"
              placeholder="예: 부산"
              {...register("region")}
            />
          </label>

          <div className="grid grid-cols-2 gap-3">
            <label className="flex flex-col gap-1">
              <span className="text-xs font-medium text-[var(--color-muted)]">최소 예산 (선택)</span>
              <input
                type="number"
                min={0}
                inputMode="numeric"
                className="h-9 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-3 text-sm tabular-nums"
                {...register("min_budget", {
                  setValueAs: (raw) => (raw === "" || raw === null ? null : Number(raw))
                })}
              />
              {errors.min_budget ? (
                <span className="text-[11px] text-[var(--color-danger)]">
                  {errors.min_budget.message}
                </span>
              ) : null}
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-xs font-medium text-[var(--color-muted)]">최대 예산 (선택)</span>
              <input
                type="number"
                min={0}
                inputMode="numeric"
                className="h-9 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-3 text-sm tabular-nums"
                {...register("max_budget", {
                  setValueAs: (raw) => (raw === "" || raw === null ? null : Number(raw))
                })}
              />
              {errors.max_budget ? (
                <span className="text-[11px] text-[var(--color-danger)]">
                  {errors.max_budget.message}
                </span>
              ) : null}
            </label>
          </div>

          <Button type="submit" className="self-start">
            <Search size={15} aria-hidden="true" /> 후보 찾기
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
