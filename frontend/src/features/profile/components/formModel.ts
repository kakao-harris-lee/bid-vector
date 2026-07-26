import type { UseFormReturn } from "react-hook-form";
import { BUSINESS_TYPE_OPTIONS } from "../constants";
import type { CompanyInfoFormValues } from "../schema";
import type { OperatorProfileResponse } from "@/shared/types/profile";
import type { OperatorStrategyResponse } from "@/shared/types/strategy";

export type EditorMode = "wizard" | "single";

export interface WizardStep {
  key: "basics" | "capacity" | "budget" | "preferences";
  title: string;
  description: string;
}

export const WIZARD_STEPS: readonly WizardStep[] = [
  {
    key: "basics",
    title: "기본 · 면허 · 지역",
    description: "업무 구분과 보유 면허, 수행 지역을 골라 매칭 기준을 만듭니다."
  },
  {
    key: "capacity",
    title: "공사 능력 · 실적",
    description: "시공능력평가액·도급한도는 공사 매칭 정확도를 크게 좌우합니다."
  },
  {
    key: "budget",
    title: "공사 가능 금액",
    description: "참여 가능한 예산 범위를 입력하세요. 빈 값은 무제한으로 간주합니다."
  },
  {
    key: "preferences",
    title: "선호 · 제외 조건",
    description: "중점 카테고리·지역과 필수/제외 키워드를 더해 후보 폭을 좁힙니다."
  }
] as const;

export const defaultValues: CompanyInfoFormValues = {
  business_type: BUSINESS_TYPE_OPTIONS[0].value,
  license_codes: [],
  region_codes: [],
  tech_fields: [],
  association_memberships: [],
  annual_revenue: 0,
  construction_capacity_amount: 0,
  awarded_contract_limit: 0,
  total_awards: 0,
  capacity_score: 0.5,
  min_budget_estimate: 0,
  max_budget_estimate: 0,
  focus_categories: [],
  focus_regions: [],
  exclude_regions: [],
  required_keywords: [],
  exclude_keywords: []
};

export interface CardProps {
  form: UseFormReturn<CompanyInfoFormValues>;
  errors: UseFormReturn<CompanyInfoFormValues>["formState"]["errors"];
}

export interface SettleResult<T> {
  value?: T;
  error?: Error;
}

export async function settle<T>(fn: () => Promise<T>): Promise<SettleResult<T>> {
  try {
    return { value: await fn() };
  } catch (err) {
    return { error: err instanceof Error ? err : new Error(String(err)) };
  }
}

export function toFormValues(
  profile: OperatorProfileResponse,
  strategy: OperatorStrategyResponse
): CompanyInfoFormValues {
  return {
    business_type: profile.business_type || BUSINESS_TYPE_OPTIONS[0].value,
    license_codes: profile.license_codes ?? [],
    region_codes: profile.region_codes ?? [],
    tech_fields: profile.tech_fields ?? [],
    association_memberships: profile.association_memberships ?? [],
    annual_revenue: profile.annual_revenue,
    construction_capacity_amount: profile.construction_capacity_amount,
    awarded_contract_limit: profile.awarded_contract_limit,
    total_awards: profile.total_awards,
    capacity_score: profile.capacity_score,
    min_budget_estimate: strategy.min_budget_estimate,
    max_budget_estimate: strategy.max_budget_estimate,
    focus_categories: strategy.focus_categories,
    focus_regions: strategy.focus_regions,
    exclude_regions: strategy.exclude_regions,
    required_keywords: strategy.required_keywords,
    exclude_keywords: strategy.exclude_keywords
  };
}
