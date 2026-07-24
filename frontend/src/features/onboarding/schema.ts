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

/**
 * 사업자번호 확인 폼 스키마(설계 §UI 1단계, #248 계약).
 * - 사업자번호: 하이픈 허용 입력이지만 숫자만 추려 정확히 10자리여야 한다(백엔드 정규화 계약).
 * - 개업일자/대표자명: 선택. 둘 다 있으면 백엔드가 진위확인, 아니면 상태조회로 분기한다.
 *
 * 원문 번호는 여기서 검증만 하고, 전송/표시는 컴포넌트가 마스킹 응답으로만 처리한다(§2).
 */
export const businessVerificationSchema = z.object({
  business_number: z
    .string()
    .min(1, "사업자번호를 입력하세요.")
    .refine(
      (raw) => raw.replace(/\D/g, "").length === 10,
      "사업자번호는 숫자 10자리입니다."
    ),
  start_date: z
    .string()
    .max(20)
    .optional()
    .refine(
      (value) => !value || /^\d{4}-?\d{2}-?\d{2}$/.test(value.trim()),
      "개업일자는 YYYY-MM-DD 또는 YYYYMMDD 형식입니다."
    ),
  representative_name: z.string().max(50).optional()
});

export type BusinessVerificationFormValues = z.infer<typeof businessVerificationSchema>;
