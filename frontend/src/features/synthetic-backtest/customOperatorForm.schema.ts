import { z } from "zod";

/** 콤마/줄바꿈 구분 텍스트 → 정규화된 string[] (빈 항목 제거, trim). */
export function textToList(value: string | undefined | null): string[] {
  if (!value) return [];
  return value
    .split(/[,\n]/)
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}

/** string[] → 콤마 구분 텍스트 (편집 초기값 채우기). */
export function listToText(value: string[] | undefined | null): string {
  if (!value || value.length === 0) return "";
  return value.join(", ");
}

const ratio = z
  .union([z.coerce.number().min(0, "0~1").max(1, "0~1"), z.literal("")])
  .optional();
const nonNegInt = z
  .union([z.coerce.number().int().min(0, "0 이상"), z.literal("")])
  .optional();
const nonNegNumber = z
  .union([z.coerce.number().min(0, "0 이상"), z.literal("")])
  .optional();

export const formSchema = z.object({
  name: z.string().trim().min(1, "회사 이름을 입력하세요."),
  slug: z.string().trim().optional(),
  company_name: z.string().trim().optional(),
  business_type: z.string().trim().optional(),
  region_codes: z.string().optional(),
  license_codes: z.string().optional(),
  annual_revenue: nonNegNumber,
  capacity_score: ratio,
  focus_categories: z.string().optional(),
  focus_regions: z.string().optional(),
  exclude_regions: z.string().optional(),
  required_keywords: z.string().optional(),
  exclude_keywords: z.string().optional(),
  min_budget_estimate: nonNegNumber,
  max_budget_estimate: nonNegNumber,
  minimum_match_score: ratio,
  minimum_probability_score: ratio,
  bid_now_threshold: ratio,
  review_threshold: ratio,
  max_recommended_candidates: nonNegInt
});

export type FormValues = z.input<typeof formSchema>;
export type FormOutput = z.output<typeof formSchema>;

export type CustomOperatorFormMode = "create" | "edit";

export function numberOrUndefined(value: number | "" | undefined): number | undefined {
  if (value === "" || value === undefined) return undefined;
  return value;
}

export function trimmedOrUndefined(value: string | undefined): string | undefined {
  const next = value?.trim();
  return next ? next : undefined;
}
