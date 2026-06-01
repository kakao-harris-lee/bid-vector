import { useMemo } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button, Card, CardContent, CardHeader, CardTitle, Input } from "@/shared/components/ui";
import type {
  CustomOperatorCreateRequest,
  CustomOperatorDetail,
  CustomOperatorUpdateRequest
} from "@/shared/types/synthetic";

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

const formSchema = z.object({
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

type FormValues = z.input<typeof formSchema>;
type FormOutput = z.output<typeof formSchema>;

export type CustomOperatorFormMode = "create" | "edit";

export interface CustomOperatorFormProps {
  mode: CustomOperatorFormMode;
  /** 편집 모드일 때 초기값 출처. 생성 모드면 생략. */
  initial?: CustomOperatorDetail | null;
  pending?: boolean;
  /** 제출 핸들러. create면 CustomOperatorCreateRequest, edit면 CustomOperatorUpdateRequest. */
  onSubmit: (payload: CustomOperatorCreateRequest | CustomOperatorUpdateRequest) => void;
  onCancel?: () => void;
}

function numberOrUndefined(value: number | "" | undefined): number | undefined {
  if (value === "" || value === undefined) return undefined;
  return value;
}

function trimmedOrUndefined(value: string | undefined): string | undefined {
  const next = value?.trim();
  return next ? next : undefined;
}

export function CustomOperatorForm({
  mode,
  initial,
  pending = false,
  onSubmit,
  onCancel
}: CustomOperatorFormProps) {
  const defaultValues = useMemo<FormValues>(() => {
    if (mode === "edit" && initial) {
      return {
        name: initial.display_name ?? "",
        slug: initial.slug ?? "",
        company_name: initial.company ?? "",
        business_type: initial.business_type ?? "",
        region_codes: listToText(initial.region_codes),
        license_codes: listToText(initial.license_codes),
        annual_revenue: initial.annual_revenue ?? "",
        capacity_score: initial.capacity_score ?? "",
        focus_categories: listToText(initial.focus_categories),
        focus_regions: listToText(initial.focus_regions),
        exclude_regions: listToText(initial.exclude_regions),
        required_keywords: listToText(initial.required_keywords),
        exclude_keywords: listToText(initial.exclude_keywords),
        min_budget_estimate: initial.min_budget_estimate ?? "",
        max_budget_estimate: initial.max_budget_estimate ?? "",
        minimum_match_score: initial.minimum_match_score ?? "",
        minimum_probability_score: initial.minimum_probability_score ?? "",
        bid_now_threshold: initial.bid_now_threshold ?? "",
        review_threshold: initial.review_threshold ?? "",
        max_recommended_candidates: initial.max_recommended_candidates ?? ""
      };
    }
    return {
      name: "",
      slug: "",
      company_name: "",
      business_type: "",
      region_codes: "",
      license_codes: "",
      annual_revenue: "",
      capacity_score: "",
      focus_categories: "",
      focus_regions: "",
      exclude_regions: "",
      required_keywords: "",
      exclude_keywords: "",
      min_budget_estimate: "",
      max_budget_estimate: "",
      minimum_match_score: "",
      minimum_probability_score: "",
      bid_now_threshold: "",
      review_threshold: "",
      max_recommended_candidates: ""
    };
  }, [mode, initial]);

  const {
    register,
    handleSubmit,
    formState: { errors }
  } = useForm<FormValues>({
    resolver: zodResolver(formSchema),
    defaultValues
  });

  const submit = handleSubmit((raw) => {
    const values = raw as FormOutput;

    // 편집은 부분 갱신이다. 비운 리스트/숫자 필드를 빈 배열/0으로 보내면
    // 서버의 기존 전략 값을 덮어쓸 수 있으므로, 편집 모드에서는 비어 있는
    // 값을 아예 omit 한다. 생성 모드는 빈 리스트를 그대로 보내도 무해하다.
    const isEdit = mode === "edit";
    const list = (value: string | undefined): string[] | undefined => {
      const parsed = textToList(value);
      if (isEdit && parsed.length === 0) return undefined;
      return parsed;
    };

    const shared = {
      company_name: trimmedOrUndefined(values.company_name),
      business_type: trimmedOrUndefined(values.business_type),
      region_codes: list(values.region_codes),
      license_codes: list(values.license_codes),
      annual_revenue: numberOrUndefined(values.annual_revenue),
      capacity_score: numberOrUndefined(values.capacity_score),
      focus_categories: list(values.focus_categories),
      focus_regions: list(values.focus_regions),
      exclude_regions: list(values.exclude_regions),
      required_keywords: list(values.required_keywords),
      exclude_keywords: list(values.exclude_keywords),
      min_budget_estimate: numberOrUndefined(values.min_budget_estimate),
      max_budget_estimate: numberOrUndefined(values.max_budget_estimate),
      minimum_match_score: numberOrUndefined(values.minimum_match_score),
      minimum_probability_score: numberOrUndefined(values.minimum_probability_score),
      bid_now_threshold: numberOrUndefined(values.bid_now_threshold),
      review_threshold: numberOrUndefined(values.review_threshold),
      max_recommended_candidates: numberOrUndefined(values.max_recommended_candidates)
    };

    if (mode === "create") {
      const payload: CustomOperatorCreateRequest = {
        name: values.name.trim(),
        slug: trimmedOrUndefined(values.slug),
        ...shared
      };
      onSubmit(payload);
      return;
    }
    const payload: CustomOperatorUpdateRequest = {
      name: values.name.trim(),
      ...shared
    };
    onSubmit(payload);
  });

  const title = mode === "create" ? "새 커스텀 회사" : `편집: ${initial?.display_name ?? ""}`;
  const submitLabel = mode === "create" ? "회사 생성" : "변경 저장";

  return (
    <Card aria-label={mode === "create" ? "커스텀 회사 생성" : "커스텀 회사 편집"}>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <form className="flex flex-col gap-4" onSubmit={submit} noValidate>
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
                <Input aria-label="slug" value={initial?.slug ?? ""} readOnly disabled />
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

          <fieldset className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <legend className="mb-1 text-xs font-medium text-[var(--color-muted)]">전략 파라미터</legend>

            <label className="flex flex-col gap-1 text-xs">
              <span className="text-[var(--color-muted)]">관심 카테고리 (콤마 구분)</span>
              <Input aria-label="관심 카테고리" {...register("focus_categories")} />
            </label>

            <label className="flex flex-col gap-1 text-xs">
              <span className="text-[var(--color-muted)]">관심 지역 (콤마 구분)</span>
              <Input aria-label="관심 지역" {...register("focus_regions")} />
            </label>

            <label className="flex flex-col gap-1 text-xs">
              <span className="text-[var(--color-muted)]">제외 지역 (콤마 구분)</span>
              <Input aria-label="제외 지역" {...register("exclude_regions")} />
            </label>

            <label className="flex flex-col gap-1 text-xs">
              <span className="text-[var(--color-muted)]">필수 키워드 (콤마 구분)</span>
              <Input aria-label="필수 키워드" {...register("required_keywords")} />
            </label>

            <label className="flex flex-col gap-1 text-xs">
              <span className="text-[var(--color-muted)]">제외 키워드 (콤마 구분)</span>
              <Input aria-label="제외 키워드" {...register("exclude_keywords")} />
            </label>

            <label className="flex flex-col gap-1 text-xs">
              <span className="text-[var(--color-muted)]">최소 추정예산 (원)</span>
              <Input
                type="number"
                min={0}
                aria-label="최소 추정예산"
                className="tabular-nums"
                {...register("min_budget_estimate")}
              />
              {errors.min_budget_estimate ? (
                <span className="text-[var(--color-danger)]">
                  {errors.min_budget_estimate.message}
                </span>
              ) : null}
            </label>

            <label className="flex flex-col gap-1 text-xs">
              <span className="text-[var(--color-muted)]">최대 추정예산 (원)</span>
              <Input
                type="number"
                min={0}
                aria-label="최대 추정예산"
                className="tabular-nums"
                {...register("max_budget_estimate")}
              />
              {errors.max_budget_estimate ? (
                <span className="text-[var(--color-danger)]">
                  {errors.max_budget_estimate.message}
                </span>
              ) : null}
            </label>

            <label className="flex flex-col gap-1 text-xs">
              <span className="text-[var(--color-muted)]">minimum_match_score (0~1)</span>
              <Input
                type="number"
                step="0.01"
                min={0}
                max={1}
                aria-label="minimum_match_score"
                className="tabular-nums"
                {...register("minimum_match_score")}
              />
              {errors.minimum_match_score ? (
                <span className="text-[var(--color-danger)]">
                  {errors.minimum_match_score.message}
                </span>
              ) : null}
            </label>

            <label className="flex flex-col gap-1 text-xs">
              <span className="text-[var(--color-muted)]">minimum_probability_score (0~1)</span>
              <Input
                type="number"
                step="0.01"
                min={0}
                max={1}
                aria-label="minimum_probability_score"
                className="tabular-nums"
                {...register("minimum_probability_score")}
              />
              {errors.minimum_probability_score ? (
                <span className="text-[var(--color-danger)]">
                  {errors.minimum_probability_score.message}
                </span>
              ) : null}
            </label>

            <label className="flex flex-col gap-1 text-xs">
              <span className="text-[var(--color-muted)]">bid_now_threshold (0~1)</span>
              <Input
                type="number"
                step="0.01"
                min={0}
                max={1}
                aria-label="bid_now_threshold"
                className="tabular-nums"
                {...register("bid_now_threshold")}
              />
              {errors.bid_now_threshold ? (
                <span className="text-[var(--color-danger)]">
                  {errors.bid_now_threshold.message}
                </span>
              ) : null}
            </label>

            <label className="flex flex-col gap-1 text-xs">
              <span className="text-[var(--color-muted)]">review_threshold (0~1)</span>
              <Input
                type="number"
                step="0.01"
                min={0}
                max={1}
                aria-label="review_threshold"
                className="tabular-nums"
                {...register("review_threshold")}
              />
              {errors.review_threshold ? (
                <span className="text-[var(--color-danger)]">
                  {errors.review_threshold.message}
                </span>
              ) : null}
            </label>

            <label className="flex flex-col gap-1 text-xs">
              <span className="text-[var(--color-muted)]">max_recommended_candidates</span>
              <Input
                type="number"
                min={0}
                aria-label="max_recommended_candidates"
                className="tabular-nums"
                {...register("max_recommended_candidates")}
              />
              {errors.max_recommended_candidates ? (
                <span className="text-[var(--color-danger)]">
                  {errors.max_recommended_candidates.message}
                </span>
              ) : null}
            </label>
          </fieldset>

          <div className="flex flex-wrap items-center gap-2">
            <Button type="submit" size="sm" disabled={pending}>
              {pending ? "저장 중…" : submitLabel}
            </Button>
            {onCancel ? (
              <Button type="button" size="sm" variant="ghost" onClick={onCancel} disabled={pending}>
                취소
              </Button>
            ) : null}
            <span className="text-xs text-[var(--color-muted)]">
              리스트 항목은 콤마(,)로 구분합니다. 비율 값은 0~1.
            </span>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}
