import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  cloneCustomOperator,
  createCustomOperator,
  deleteCustomOperator,
  fetchSyntheticOperators,
  updateCustomOperator
} from "@/shared/api";
import { Badge, Button, Card, CardContent, CardHeader, CardTitle, toastApi } from "@/shared/components/ui";
import { formatCurrency } from "@/shared/lib";
import type {
  CustomOperatorCreateRequest,
  CustomOperatorDetail,
  CustomOperatorUpdateRequest,
  SyntheticOperatorItem
} from "@/shared/types/synthetic";
import { CustomOperatorForm } from "./CustomOperatorForm";

export interface CustomOperatorManagerProps {
  token?: string | null;
}

type Editor =
  | { kind: "create" }
  | { kind: "edit"; detail: CustomOperatorDetail }
  | null;

const OPERATORS_KEY = ["synthetic", "operators"];

function formatRevenue(value: number): string {
  if (!value) return "—";
  return formatCurrency(value);
}

export function CustomOperatorManager({ token }: CustomOperatorManagerProps) {
  const queryClient = useQueryClient();
  const [editor, setEditor] = useState<Editor>(null);
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);

  const operators = useQuery({
    queryKey: OPERATORS_KEY,
    queryFn: () => fetchSyntheticOperators(token),
    enabled: Boolean(token)
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: OPERATORS_KEY });

  const createMutation = useMutation<CustomOperatorDetail, Error, CustomOperatorCreateRequest>({
    mutationFn: (payload) => createCustomOperator(payload, token),
    onSuccess: (detail) => {
      void invalidate();
      setEditor(null);
      toastApi.success({
        title: "회사 생성됨",
        description: `"${detail.display_name}"을(를) 생성했습니다.`
      });
    },
    onError: (err) =>
      toastApi.danger({
        title: "회사 생성 실패",
        description: err instanceof Error ? err.message : "알 수 없는 오류"
      })
  });

  const updateMutation = useMutation<
    CustomOperatorDetail,
    Error,
    { slug: string; payload: CustomOperatorUpdateRequest }
  >({
    mutationFn: ({ slug, payload }) => updateCustomOperator(slug, payload, token),
    onSuccess: (detail) => {
      void invalidate();
      setEditor(null);
      toastApi.success({
        title: "회사 편집됨",
        description: `"${detail.display_name}"을(를) 갱신했습니다.`
      });
    },
    onError: (err) =>
      toastApi.danger({
        title: "회사 편집 실패",
        description: err instanceof Error ? err.message : "알 수 없는 오류"
      })
  });

  const cloneMutation = useMutation<CustomOperatorDetail, Error, SyntheticOperatorItem>({
    mutationFn: (operator) =>
      cloneCustomOperator(operator.slug, { name: `${operator.display_name} 복제` }, token),
    onSuccess: (detail) => {
      void invalidate();
      toastApi.success({
        title: "복제 완료",
        description: `"${detail.display_name}"으로 복제했습니다.`
      });
    },
    onError: (err) =>
      toastApi.danger({
        title: "복제 실패",
        description: err instanceof Error ? err.message : "알 수 없는 오류"
      })
  });

  const deleteMutation = useMutation<{ slug: string }, Error, string>({
    mutationFn: async (slug) => {
      const result = await deleteCustomOperator(slug, token);
      return { slug: result.slug };
    },
    onSuccess: (result) => {
      void invalidate();
      setPendingDelete(null);
      if (editor?.kind === "edit" && editor.detail.slug === result.slug) {
        setEditor(null);
      }
      toastApi.success({ title: "삭제됨", description: `"${result.slug}" 회사를 삭제했습니다.` });
    },
    onError: (err) => {
      setPendingDelete(null);
      toastApi.danger({
        title: "삭제 실패",
        description: err instanceof Error ? err.message : "알 수 없는 오류"
      });
    }
  });

  const items = operators.data?.operators ?? [];
  const customItems = items.filter((operator) => operator.is_custom);
  const presetItems = items.filter((operator) => !operator.is_custom);

  const handleSubmit = (payload: CustomOperatorCreateRequest | CustomOperatorUpdateRequest) => {
    if (editor?.kind === "create") {
      createMutation.mutate(payload as CustomOperatorCreateRequest);
      return;
    }
    if (editor?.kind === "edit") {
      updateMutation.mutate({
        slug: editor.detail.slug,
        payload: payload as CustomOperatorUpdateRequest
      });
    }
  };

  const formPending = createMutation.isPending || updateMutation.isPending;

  if (editor) {
    return (
      <CustomOperatorForm
        mode={editor.kind === "create" ? "create" : "edit"}
        initial={editor.kind === "edit" ? editor.detail : null}
        pending={formPending}
        onSubmit={handleSubmit}
        onCancel={() => setEditor(null)}
      />
    );
  }

  return (
    <Card aria-label="가상 회사 관리">
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle>
          가상 회사 관리
          <span className="ml-2 text-xs font-normal text-[var(--color-muted)]">
            프리셋 {presetItems.length} · 커스텀 {customItems.length}
          </span>
        </CardTitle>
        <Button type="button" size="sm" onClick={() => setEditor({ kind: "create" })}>
          새 커스텀 회사
        </Button>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        {operators.isLoading ? (
          <p className="text-xs text-[var(--color-muted)]">불러오는 중…</p>
        ) : null}

        <section className="flex flex-col gap-2" aria-label="커스텀 회사 목록">
          <h3 className="text-xs font-semibold text-[var(--color-fg)]">커스텀 회사</h3>
          {customItems.length === 0 && !operators.isLoading ? (
            <p className="text-xs text-[var(--color-muted)]">
              커스텀 회사가 없습니다. "새 커스텀 회사"로 만들거나 프리셋을 복제하세요.
            </p>
          ) : null}
          <ul className="flex flex-col gap-2">
            {customItems.map((operator) => (
              <li
                key={operator.slug}
                className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-3 py-2"
              >
                <span className="flex min-w-0 flex-col">
                  <span className="flex items-center gap-2">
                    <span className="truncate text-sm font-medium text-[var(--color-fg)]">
                      {operator.display_name}
                    </span>
                    <Badge tone="info">커스텀</Badge>
                  </span>
                  <span className="text-xs text-[var(--color-muted)]">
                    {operator.slug} · 매출 {formatRevenue(operator.annual_revenue)}
                  </span>
                </span>
                <span className="flex items-center gap-1">
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => setEditor({ kind: "edit", detail: toDetail(operator) })}
                  >
                    편집
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    disabled={cloneMutation.isPending}
                    onClick={() => cloneMutation.mutate(operator)}
                  >
                    복제
                  </Button>
                  {pendingDelete === operator.slug ? (
                    <span className="flex items-center gap-1">
                      <Button
                        type="button"
                        size="sm"
                        variant="destructive"
                        disabled={deleteMutation.isPending}
                        onClick={() => deleteMutation.mutate(operator.slug)}
                      >
                        삭제 확인
                      </Button>
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        onClick={() => setPendingDelete(null)}
                      >
                        취소
                      </Button>
                    </span>
                  ) : (
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      onClick={() => setPendingDelete(operator.slug)}
                    >
                      삭제
                    </Button>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </section>

        <section className="flex flex-col gap-2" aria-label="프리셋 회사 목록">
          <h3 className="text-xs font-semibold text-[var(--color-fg)]">프리셋 (보호됨)</h3>
          <p className="text-xs text-[var(--color-muted)]">
            프리셋은 편집·삭제할 수 없습니다. 복제해서 커스텀으로 수정하세요.
          </p>
          <ul className="flex flex-col gap-2">
            {presetItems.map((operator) => (
              <li
                key={operator.slug}
                className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-3 py-2"
              >
                <span className="flex min-w-0 flex-col">
                  <span className="flex items-center gap-2">
                    <span className="truncate text-sm font-medium text-[var(--color-fg)]">
                      {operator.display_name}
                    </span>
                    <Badge tone="muted">프리셋</Badge>
                  </span>
                  <span className="text-xs text-[var(--color-muted)]">
                    {operator.slug} · 매출 {formatRevenue(operator.annual_revenue)}
                  </span>
                </span>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={cloneMutation.isPending}
                  onClick={() => cloneMutation.mutate(operator)}
                >
                  복제
                </Button>
              </li>
            ))}
          </ul>
        </section>
      </CardContent>
    </Card>
  );
}

/**
 * 목록(GET /operators)의 경량 항목을 편집 폼이 쓰는 detail 형태로 승격한다.
 * 목록에 없는 전략 필드는 백엔드 기본값(0/빈 배열)으로 채운다 — 편집 시 비운
 * 필드는 미전송(부분 갱신)이므로 서버 값이 유지된다.
 */
function toDetail(operator: SyntheticOperatorItem): CustomOperatorDetail {
  return {
    user_id: operator.user_id,
    username: operator.username,
    slug: operator.slug,
    is_custom: operator.is_custom,
    display_name: operator.display_name,
    company: operator.company ?? null,
    business_type: operator.business_type ?? null,
    annual_revenue: operator.annual_revenue,
    capacity_score: operator.capacity_score,
    license_codes: [],
    region_codes: [],
    focus_categories: [],
    focus_regions: [],
    exclude_regions: [],
    required_keywords: [],
    exclude_keywords: [],
    min_budget_estimate: 0,
    max_budget_estimate: 0,
    minimum_match_score: 0,
    minimum_probability_score: 0,
    bid_now_threshold: operator.bid_now_threshold,
    review_threshold: operator.review_threshold,
    max_recommended_candidates: 0
  };
}
