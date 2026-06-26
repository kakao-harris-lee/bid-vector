import type { UseFormRegisterReturn } from "react-hook-form";

export interface NumberFieldProps {
  label: string;
  /** `register("field", { valueAsNumber: true })` 결과를 그대로 전달. */
  register: UseFormRegisterReturn;
  error?: string;
  min?: number;
  max?: number;
  step?: number;
}

/**
 * react-hook-form `register`로 묶는 숫자 입력 필드.
 *
 * StrategyEditor와 CompanyInfoEditor가 동일하게 정의하던 컴포넌트를
 * 한 곳으로 모은 것입니다(label/min/max/step/error + tabular-nums).
 */
export function NumberField({
  label,
  register,
  error,
  min,
  max,
  step
}: NumberFieldProps) {
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
