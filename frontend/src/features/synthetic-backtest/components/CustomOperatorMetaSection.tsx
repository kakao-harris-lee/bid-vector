import type { FieldErrors, UseFormRegister } from "react-hook-form";
import { Input } from "@/shared/components/ui";
import type {
  CustomOperatorFormMode,
  FormValues
} from "../customOperatorForm.schema";

export function CustomOperatorMetaSection({
  mode,
  initialSlug,
  register,
  errors
}: {
  mode: CustomOperatorFormMode;
  initialSlug?: string | null;
  register: UseFormRegister<FormValues>;
  errors: FieldErrors<FormValues>;
}) {
  return (
    <fieldset className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      <legend className="mb-1 text-xs font-medium text-[var(--color-muted)]">회사 메타</legend>

      <label className="flex flex-col gap-1 text-xs">
        <span className="text-[var(--color-muted)]">이름 *</span>
        <Input aria-label="이름" placeholder="예: 우리 회사" {...register("name")} />
        {errors.name ? (
          <span className="text-[var(--color-danger)]">{errors.name.message}</span>
        ) : null}
      </label>

      {mode === "create" ? (
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-[var(--color-muted)]">slug (선택)</span>
          <Input aria-label="slug" placeholder="비우면 이름에서 생성" {...register("slug")} />
        </label>
      ) : (
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-[var(--color-muted)]">slug (고정)</span>
          <Input aria-label="slug" value={initialSlug ?? ""} readOnly disabled />
        </label>
      )}

      <label className="flex flex-col gap-1 text-xs">
        <span className="text-[var(--color-muted)]">상호명 (선택)</span>
        <Input aria-label="상호명" {...register("company_name")} />
      </label>

      <label className="flex flex-col gap-1 text-xs">
        <span className="text-[var(--color-muted)]">업종 (선택)</span>
        <Input aria-label="업종" placeholder="예: 공사 / 용역" {...register("business_type")} />
      </label>

      <label className="flex flex-col gap-1 text-xs">
        <span className="text-[var(--color-muted)]">연 매출 (원)</span>
        <Input
          type="number"
          min={0}
          aria-label="연 매출"
          className="tabular-nums"
          {...register("annual_revenue")}
        />
        {errors.annual_revenue ? (
          <span className="text-[var(--color-danger)]">{errors.annual_revenue.message}</span>
        ) : null}
      </label>

      <label className="flex flex-col gap-1 text-xs">
        <span className="text-[var(--color-muted)]">capacity_score (0~1)</span>
        <Input
          type="number"
          step="0.01"
          min={0}
          max={1}
          aria-label="capacity_score"
          className="tabular-nums"
          {...register("capacity_score")}
        />
        {errors.capacity_score ? (
          <span className="text-[var(--color-danger)]">{errors.capacity_score.message}</span>
        ) : null}
      </label>

      <label className="flex flex-col gap-1 text-xs">
        <span className="text-[var(--color-muted)]">지역 코드 (콤마 구분)</span>
        <Input aria-label="지역 코드" placeholder="예: 11, 41" {...register("region_codes")} />
      </label>

      <label className="flex flex-col gap-1 text-xs">
        <span className="text-[var(--color-muted)]">면허 코드 (콤마 구분)</span>
        <Input aria-label="면허 코드" placeholder="예: 0001, 0002" {...register("license_codes")} />
      </label>
    </fieldset>
  );
}
