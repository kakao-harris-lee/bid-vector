import type { FieldErrors, UseFormRegister } from "react-hook-form";
import { Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import { NumberField } from "@/shared/components";
import type { StrategyFormValues } from "../schema";

export function StrategyBudgetSection({
  register,
  errors
}: {
  register: UseFormRegister<StrategyFormValues>;
  errors: FieldErrors<StrategyFormValues>;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>예산 범위</CardTitle>
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <NumberField
          label="최소 예산 (원)"
          register={register("min_budget_estimate", { valueAsNumber: true })}
          error={errors.min_budget_estimate?.message}
          step={1_000_000}
        />
        <NumberField
          label="최대 예산 (원, 0=무제한)"
          register={register("max_budget_estimate", { valueAsNumber: true })}
          error={errors.max_budget_estimate?.message}
          step={1_000_000}
        />
      </CardContent>
    </Card>
  );
}
