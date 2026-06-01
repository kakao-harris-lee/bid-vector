import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useMutation, useQuery } from "@tanstack/react-query";
import { createExperiment, fetchSyntheticOperators } from "@/shared/api";
import { Button, Card, CardContent, CardHeader, CardTitle, Input, toastApi } from "@/shared/components/ui";
import type {
  SyntheticExperimentCreateRequest,
  SyntheticExperimentResponse
} from "@/shared/types/synthetic";

const experimentSchema = z.object({
  name: z.string().trim().min(1, "실험 이름을 입력하세요."),
  description: z.string().trim().optional(),
  start_at: z.string().optional(),
  end_at: z.string().optional(),
  category: z.string().trim().optional(),
  limit: z.coerce.number().int().min(1, "1 이상").max(1000, "1000 이하"),
  scenario: z.string().trim().min(1, "시나리오를 입력하세요."),
  cutoff_hours: z.union([z.coerce.number().min(0), z.literal("")]).optional(),
  history_limit: z.union([z.coerce.number().int().min(0), z.literal("")]).optional()
});

type ExperimentFormValues = z.input<typeof experimentSchema>;
type ExperimentFormOutput = z.output<typeof experimentSchema>;

export interface ExperimentFormProps {
  /** 운영자 세션 토큰. */
  token?: string | null;
  /** 실험 생성 성공 시 호출(생성된 실험을 선택 상태로). */
  onCreated?: (experiment: SyntheticExperimentResponse) => void;
}

function toIsoOrNull(value?: string): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return date.toISOString();
}

function toNumberOrNull(value: number | "" | undefined): number | null {
  if (value === "" || value === undefined) return null;
  return value;
}

export function ExperimentForm({ token, onCreated }: ExperimentFormProps) {
  const [selectedSlugs, setSelectedSlugs] = useState<string[]>([]);

  const operators = useQuery({
    queryKey: ["synthetic", "operators"],
    queryFn: () => fetchSyntheticOperators(token),
    enabled: Boolean(token)
  });

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors }
  } = useForm<ExperimentFormValues>({
    resolver: zodResolver(experimentSchema),
    defaultValues: {
      name: "",
      description: "",
      start_at: "",
      end_at: "",
      category: "",
      limit: 100,
      scenario: "base",
      cutoff_hours: "",
      history_limit: ""
    }
  });

  const createMutation = useMutation<
    SyntheticExperimentResponse,
    Error,
    SyntheticExperimentCreateRequest
  >({
    mutationFn: (payload) => createExperiment(payload, token),
    onSuccess: (experiment) => {
      toastApi.success({
        title: "실험 생성됨",
        description: `"${experiment.name}" 실험을 저장했습니다.`
      });
      reset();
      setSelectedSlugs([]);
      onCreated?.(experiment);
    },
    onError: (err) =>
      toastApi.danger({
        title: "실험 생성 실패",
        description: err instanceof Error ? err.message : "알 수 없는 오류"
      })
  });

  const toggleSlug = (slug: string, checked: boolean) => {
    setSelectedSlugs((current) =>
      checked ? [...current, slug] : current.filter((value) => value !== slug)
    );
  };

  const onSubmit = handleSubmit((values) => {
    const parsed = values as ExperimentFormOutput;
    const payload: SyntheticExperimentCreateRequest = {
      name: parsed.name.trim(),
      description: parsed.description?.trim() ? parsed.description.trim() : null,
      params: {
        start_at: toIsoOrNull(parsed.start_at),
        end_at: toIsoOrNull(parsed.end_at),
        category: parsed.category?.trim() ? parsed.category.trim() : null,
        limit: parsed.limit,
        scenario: parsed.scenario.trim(),
        cutoff_hours: toNumberOrNull(parsed.cutoff_hours),
        history_limit: toNumberOrNull(parsed.history_limit),
        settle_actions: false
      },
      operator_slugs: selectedSlugs.length > 0 ? selectedSlugs : null
    };
    createMutation.mutate(payload);
  });

  const operatorItems = operators.data?.operators ?? [];

  return (
    <Card aria-label="실험 생성">
      <CardHeader>
        <CardTitle>새 실험</CardTitle>
      </CardHeader>
      <CardContent>
        <form className="flex flex-col gap-4" onSubmit={onSubmit} noValidate>
          <label className="flex flex-col gap-1 text-xs">
            <span className="text-[var(--color-muted)]">이름</span>
            <Input
              aria-label="이름"
              placeholder="예: 2025 상반기 용역 비교"
              {...register("name")}
            />
            {errors.name ? (
              <span className="text-[var(--color-danger)]">{errors.name.message}</span>
            ) : null}
          </label>

          <label className="flex flex-col gap-1 text-xs">
            <span className="text-[var(--color-muted)]">설명 (선택)</span>
            <textarea
              aria-label="설명"
              rows={2}
              placeholder="실험 의도/조건 메모"
              className="rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-2 py-1 text-sm"
              {...register("description")}
            />
          </label>

          <fieldset className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <legend className="mb-1 text-xs font-medium text-[var(--color-muted)]">조건</legend>
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-[var(--color-muted)]">시작일 (선택)</span>
              <Input type="date" aria-label="시작일" {...register("start_at")} />
            </label>
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-[var(--color-muted)]">종료일 (선택)</span>
              <Input type="date" aria-label="종료일" {...register("end_at")} />
            </label>
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-[var(--color-muted)]">카테고리 (선택)</span>
              <Input aria-label="카테고리" placeholder="예: 용역" {...register("category")} />
            </label>
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-[var(--color-muted)]">limit</span>
              <Input
                type="number"
                min={1}
                max={1000}
                aria-label="limit"
                className="tabular-nums"
                {...register("limit")}
              />
              {errors.limit ? (
                <span className="text-[var(--color-danger)]">{errors.limit.message}</span>
              ) : null}
            </label>
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-[var(--color-muted)]">시나리오</span>
              <Input aria-label="시나리오" {...register("scenario")} />
              {errors.scenario ? (
                <span className="text-[var(--color-danger)]">{errors.scenario.message}</span>
              ) : null}
            </label>
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-[var(--color-muted)]">cutoff_hours (선택)</span>
              <Input type="number" min={0} aria-label="cutoff_hours" {...register("cutoff_hours")} />
            </label>
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-[var(--color-muted)]">history_limit (선택)</span>
              <Input type="number" min={0} aria-label="history_limit" {...register("history_limit")} />
            </label>
          </fieldset>

          <fieldset className="flex flex-col gap-2">
            <legend className="mb-1 text-xs font-medium text-[var(--color-muted)]">
              참여 회사 (비우면 전체)
            </legend>
            {operators.isLoading ? (
              <p className="text-xs text-[var(--color-muted)]">회사 목록 불러오는 중…</p>
            ) : null}
            {operators.data && operatorItems.length === 0 ? (
              <p className="text-xs text-[var(--color-muted)]">
                시드된 synthetic 회사가 없습니다. "비교 / 시드" 탭에서 먼저 시드하세요.
              </p>
            ) : null}
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {operatorItems.map((operator) => {
                const checked = selectedSlugs.includes(operator.slug);
                return (
                  <label
                    key={operator.slug}
                    className="flex items-center gap-2 text-sm text-[var(--color-fg)]"
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      aria-label={operator.display_name}
                      onChange={(event) => toggleSlug(operator.slug, event.target.checked)}
                    />
                    <span>{operator.display_name}</span>
                    <span className="text-xs text-[var(--color-muted)]">({operator.slug})</span>
                  </label>
                );
              })}
            </div>
          </fieldset>

          <div className="flex flex-wrap items-center gap-2">
            <Button type="submit" size="sm" disabled={createMutation.isPending}>
              {createMutation.isPending ? "저장 중…" : "실험 저장"}
            </Button>
            <span className="text-xs text-[var(--color-muted)]">
              win_rate는 가격 기준 추정 낙찰(would_have_won_price_only_count / settled_count). 실제 낙찰이 아님.
            </span>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}
