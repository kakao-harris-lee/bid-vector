import { z } from "zod";

const ratio = z
  .number({ message: "0과 1 사이의 숫자여야 합니다." })
  .min(0, "0 이상이어야 합니다.")
  .max(1, "1 이하여야 합니다.");

const budget = z
  .number({ message: "0 이상의 숫자여야 합니다." })
  .min(0, "0 이상이어야 합니다.");

export const strategyFormSchema = z
  .object({
    focus_categories: z.array(z.string().min(1)).max(50),
    focus_regions: z.array(z.string().min(1)).max(50),
    exclude_regions: z.array(z.string().min(1)).max(50),
    required_keywords: z.array(z.string().min(1)).max(50),
    exclude_keywords: z.array(z.string().min(1)).max(50),
    min_budget_estimate: budget,
    max_budget_estimate: budget,
    minimum_match_score: ratio,
    minimum_probability_score: ratio,
    bid_now_threshold: ratio,
    review_threshold: ratio,
    auto_workload_penalty_multiplier: z
      .number({ message: "0~2 사이의 숫자여야 합니다." })
      .min(0)
      .max(2),
    max_recommended_candidates: z
      .number({ message: "1~100 사이의 정수여야 합니다." })
      .int("정수여야 합니다.")
      .min(1)
      .max(100),
    notify_only_high_priority: z.boolean()
  })
  .superRefine((value, ctx) => {
    if (value.review_threshold > value.bid_now_threshold) {
      ctx.addIssue({
        code: "custom",
        path: ["review_threshold"],
        message: "검토 임계값은 즉시 투찰 임계값보다 클 수 없습니다."
      });
      ctx.addIssue({
        code: "custom",
        path: ["bid_now_threshold"],
        message: "즉시 투찰 임계값은 검토 임계값보다 작을 수 없습니다."
      });
    }
    if (
      value.max_budget_estimate > 0 &&
      value.min_budget_estimate > value.max_budget_estimate
    ) {
      ctx.addIssue({
        code: "custom",
        path: ["max_budget_estimate"],
        message: "최대 예산은 최소 예산보다 크거나 같아야 합니다."
      });
    }
  });

export type StrategyFormValues = z.infer<typeof strategyFormSchema>;
