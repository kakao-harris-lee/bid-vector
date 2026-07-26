import { Controller } from "react-hook-form";
import { Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import { NumberField, ThresholdControl } from "@/shared/components";
import type { CardProps } from "./formModel";

export function CapacityCard({ form, errors }: CardProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>공사 능력 · 실적</CardTitle>
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <NumberField
          label="연매출 (원)"
          register={form.register("annual_revenue", { valueAsNumber: true })}
          error={errors.annual_revenue?.message}
          min={0}
          step={10_000_000}
        />
        <NumberField
          label="누적 낙찰 실적 (건)"
          register={form.register("total_awards", { valueAsNumber: true })}
          error={errors.total_awards?.message}
          min={0}
          step={1}
        />
        <NumberField
          label="시공능력평가액 (원, 0=미입력)"
          register={form.register("construction_capacity_amount", {
            valueAsNumber: true
          })}
          error={errors.construction_capacity_amount?.message}
          min={0}
          step={10_000_000}
        />
        <NumberField
          label="도급한도 (원, 0=미입력)"
          register={form.register("awarded_contract_limit", { valueAsNumber: true })}
          error={errors.awarded_contract_limit?.message}
          min={0}
          step={10_000_000}
        />
        <div className="sm:col-span-2">
          <Controller
            control={form.control}
            name="capacity_score"
            render={({ field }) => (
              <ThresholdControl
                label="수행 역량 (0~1)"
                value={field.value}
                onChange={field.onChange}
                min={0}
                max={1}
                step={0.05}
                description="현재 동시 수행 가능한 역량 수준. 1에 가까울수록 여유."
                error={errors.capacity_score?.message}
              />
            )}
          />
        </div>
      </CardContent>
    </Card>
  );
}
