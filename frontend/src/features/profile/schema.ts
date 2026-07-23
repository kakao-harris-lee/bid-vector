import { z } from "zod";

const nonNegative = z
  .number({ message: "0 이상의 숫자여야 합니다." })
  .min(0, "0 이상이어야 합니다.");

const chips = z.array(z.string().min(1)).max(50);

export const companyInfoFormSchema = z
  .object({
    // --- profile-owned -------------------------------------------------------
    business_type: z.string().min(1, "업무 구분을 선택하세요."),
    license_codes: chips,
    region_codes: chips,
    // cohort 정체성(기술부문·협회 가입) — license/region 과 동일한 chips 형태.
    tech_fields: chips,
    association_memberships: chips,
    annual_revenue: nonNegative,
    construction_capacity_amount: nonNegative,
    awarded_contract_limit: nonNegative,
    total_awards: z
      .number({ message: "0 이상의 정수여야 합니다." })
      .int("정수여야 합니다.")
      .min(0, "0 이상이어야 합니다."),
    capacity_score: z
      .number({ message: "0과 1 사이의 숫자여야 합니다." })
      .min(0, "0 이상이어야 합니다.")
      .max(1, "1 이하여야 합니다."),
    // --- strategy-owned (partial update; thresholds untouched) ---------------
    min_budget_estimate: nonNegative,
    max_budget_estimate: nonNegative,
    focus_categories: chips,
    focus_regions: chips,
    exclude_regions: chips,
    required_keywords: chips,
    exclude_keywords: chips
  })
  .superRefine((value, ctx) => {
    if (
      value.max_budget_estimate > 0 &&
      value.min_budget_estimate > value.max_budget_estimate
    ) {
      ctx.addIssue({
        code: "custom",
        path: ["max_budget_estimate"],
        message: "최대 금액은 최소 금액보다 크거나 같아야 합니다."
      });
    }
  });

export type CompanyInfoFormValues = z.infer<typeof companyInfoFormSchema>;
