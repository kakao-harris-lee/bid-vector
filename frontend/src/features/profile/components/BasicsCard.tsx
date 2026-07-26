import { Controller } from "react-hook-form";
import { Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import { ChipInput } from "@/shared/components";
import {
  BUSINESS_TYPE_OPTIONS,
  COHORT_CHIP_FIELDS,
  LICENSE_CHIPS,
  RECORD_ONLY_LICENSE_CHIPS,
  REGION_CHIPS,
  type LicenseChip
} from "../constants";
import type { CardProps } from "./formModel";

export function BasicsCard({ form, errors }: CardProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>기본 · 면허 · 지역</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium text-[var(--color-muted)]">업무 구분</span>
          <select
            className="h-9 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-3 text-sm"
            aria-label="업무 구분"
            {...form.register("business_type")}
          >
            {BUSINESS_TYPE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          {errors.business_type ? (
            <span className="text-[11px] text-[var(--color-danger)]">
              {errors.business_type.message}
            </span>
          ) : null}
        </label>

        <Controller
          control={form.control}
          name="license_codes"
          render={({ field }) => (
            <div className="flex flex-col gap-2">
              <ChipInput
                label="보유 면허"
                value={field.value}
                onChange={field.onChange}
                placeholder="면허명 또는 코드 입력"
              />
              <LicenseSuggestions value={field.value} onChange={field.onChange} />
            </div>
          )}
        />

        <Controller
          control={form.control}
          name="region_codes"
          render={({ field }) => (
            <div className="flex flex-col gap-2">
              <ChipInput
                label="수행 지역"
                value={field.value}
                onChange={field.onChange}
                placeholder="지역명 입력"
              />
              <RegionSuggestions value={field.value} onChange={field.onChange} />
            </div>
          )}
        />

        <Controller
          control={form.control}
          name="tech_fields"
          render={({ field }) => (
            <ChipInput
              label={COHORT_CHIP_FIELDS.tech_fields.label}
              value={field.value}
              onChange={field.onChange}
              placeholder={COHORT_CHIP_FIELDS.tech_fields.placeholder}
            />
          )}
        />

        <Controller
          control={form.control}
          name="association_memberships"
          render={({ field }) => (
            <ChipInput
              label={COHORT_CHIP_FIELDS.association_memberships.label}
              value={field.value}
              onChange={field.onChange}
              placeholder={COHORT_CHIP_FIELDS.association_memberships.placeholder}
            />
          )}
        />
      </CardContent>
    </Card>
  );
}

function addToken(value: string[], onChange: (next: string[]) => void, token: string) {
  if (value.includes(token)) return;
  onChange([...value, token]);
}

function LicenseSuggestions({
  value,
  onChange
}: {
  value: string[];
  onChange: (next: string[]) => void;
}) {
  // The classifier now recognises every curated chip (including the eight
  // construction licenses), so by default there is nothing to flag as
  // record-only. Keep the note conditional in case record-only chips are
  // reintroduced later.
  const hasRecordOnly = RECORD_ONLY_LICENSE_CHIPS.length > 0;
  return (
    <div className="flex flex-col gap-1">
      <span className="text-[11px] text-[var(--color-muted)]">
        추천 면허 (클릭하면 추가)
      </span>
      <div className="flex flex-wrap gap-1.5">
        {LICENSE_CHIPS.map((chip) => (
          <LicenseChipButton
            key={chip.value}
            chip={chip}
            active={value.includes(chip.value)}
            onClick={() => addToken(value, onChange, chip.value)}
          />
        ))}
      </div>
      <span className="text-[11px] text-[var(--color-muted)]">
        {hasRecordOnly
          ? "⚠ 표시 없는 면허만 공고 요구 면허와 자동 매칭됩니다. 그 외는 기록용입니다."
          : "추천 면허는 모두 공고 요구 면허와 자동 매칭됩니다."}
      </span>
    </div>
  );
}

function LicenseChipButton({
  chip,
  active,
  onClick
}: {
  chip: LicenseChip;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={`보유 면허에 ${chip.label} 추가`}
      aria-pressed={active}
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs transition-colors ${
        active
          ? "border-[var(--color-primary)] bg-[var(--color-primary)] text-[var(--color-primary-foreground)]"
          : "border-[var(--color-border)] bg-[var(--color-card)] text-[var(--color-fg)] hover:border-[var(--color-primary)]"
      }`}
    >
      <span>{chip.label}</span>
      {chip.matchable ? null : <span aria-hidden="true">⚠</span>}
    </button>
  );
}

function RegionSuggestions({
  value,
  onChange
}: {
  value: string[];
  onChange: (next: string[]) => void;
}) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-[11px] text-[var(--color-muted)]">
        추천 지역 (클릭하면 추가)
      </span>
      <div className="flex flex-wrap gap-1.5">
        {REGION_CHIPS.map((region) => {
          const active = value.includes(region);
          return (
            <button
              key={region}
              type="button"
              onClick={() => addToken(value, onChange, region)}
              aria-label={`수행 지역에 ${region} 추가`}
              aria-pressed={active}
              className={`rounded-full border px-2 py-0.5 text-xs transition-colors ${
                active
                  ? "border-[var(--color-primary)] bg-[var(--color-primary)] text-[var(--color-primary-foreground)]"
                  : "border-[var(--color-border)] bg-[var(--color-card)] text-[var(--color-fg)] hover:border-[var(--color-primary)]"
              }`}
            >
              {region}
            </button>
          );
        })}
      </div>
    </div>
  );
}
