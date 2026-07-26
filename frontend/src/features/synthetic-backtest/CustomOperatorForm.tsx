import { useMemo } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { Button, Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import type {
  CustomOperatorCreateRequest,
  CustomOperatorDetail,
  CustomOperatorUpdateRequest
} from "@/shared/types/synthetic";
import {
  formSchema,
  listToText,
  numberOrUndefined,
  textToList,
  trimmedOrUndefined,
  type CustomOperatorFormMode,
  type FormOutput,
  type FormValues
} from "./customOperatorForm.schema";
import {
  CustomOperatorMetaSection,
  CustomOperatorStrategySection
} from "./components";

export { textToList, listToText };
export type { CustomOperatorFormMode };

export interface CustomOperatorFormProps {
  mode: CustomOperatorFormMode;
  /** 편집 모드일 때 초기값 출처. 생성 모드면 생략. */
  initial?: CustomOperatorDetail | null;
  pending?: boolean;
  /** 제출 핸들러. create면 CustomOperatorCreateRequest, edit면 CustomOperatorUpdateRequest. */
  onSubmit: (payload: CustomOperatorCreateRequest | CustomOperatorUpdateRequest) => void;
  onCancel?: () => void;
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
          <CustomOperatorMetaSection
            mode={mode}
            initialSlug={initial?.slug}
            register={register}
            errors={errors}
          />

          <CustomOperatorStrategySection register={register} errors={errors} />

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
