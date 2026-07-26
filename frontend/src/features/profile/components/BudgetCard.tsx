import { Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import { NumberField } from "@/shared/components";
import type { CardProps } from "./formModel";

export function BudgetCard({ form, errors }: CardProps) {
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
