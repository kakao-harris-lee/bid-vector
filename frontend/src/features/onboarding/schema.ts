import { z } from "zod";

/**
 * seed 입력 폼 스키마(설계 §UI: 진입점은 기본 후보 = 키워드/지역/예산 seed).
 * 키워드는 최소 1개 필수(백엔드 422 계약과 정합). 지역/예산은 선택 힌트.
 */
export const onboardingSeedSchema = z
  .object({
    keywords: z
      .array(z.string().min(1))
      .min(1, "키워드를 최소 1개 입력하세요.")
      .max(20, "키워드는 최대 20개까지 입력할 수 있습니다."),
    region: z.string().max(100).optional(),
    min_budget: z
      .number({ message: "0 이상의 숫자여야 합니다." })
      .min(0, "0 이상이어야 합니다.")
      .nullable()
      .optional(),
    max_budget: z
      .number({ message: "0 이상의 숫자여야 합니다." })
      .min(0, "0 이상이어야 합니다.")
      .nullable()
      .optional()
  })
  .superRefine((value, ctx) => {
    if (
      typeof value.min_budget === "number" &&
      typeof value.max_budget === "number" &&
      value.max_budget > 0 &&
      value.min_budget > value.max_budget
    ) {
      ctx.addIssue({
        code: "custom",
        path: ["max_budget"],
        message: "예산 상한은 하한보다 크거나 같아야 합니다."
      });
    }
  });

export type OnboardingSeedFormValues = z.infer<typeof onboardingSeedSchema>;
