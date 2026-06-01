import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  cloneCustomOperator,
  createCustomOperator,
  deleteCustomOperator,
  fetchSyntheticOperators,
  getCustomOperator,
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
  | { kind: "edit"; slug: string }
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

  const editingSlug = editor?.kind === "edit" ? editor.slug : null;
  // 편집 진입 시 단건 GET으로 전략 전 필드를 정확히 프리필한다(목록은 경량이라
  // 전략 필드가 비어 있음). 응답이 오기 전까지 폼은 로딩 상태로 둔다.
  const editDetail = useQuery({
    queryKey: ["synthetic", "custom-operators", editingSlug],
    queryFn: () => getCustomOperator(editingSlug as string, token),
    enabled: Boolean(editingSlug)
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
      if (editor?.kind === "edit" && editor.slug === result.slug) {
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
        slug: editor.slug,
        payload: payload as CustomOperatorUpdateRequest
      });
    }
  };

  const formPending = createMutation.isPending || updateMutation.isPending;

  if (editor?.kind === "create") {
    return (
      <CustomOperatorForm
        mode="create"
        initial={null}
        pending={formPending}
        onSubmit={handleSubmit}
        onCancel={() => setEditor(null)}
      />
    );
  }

  if (editor?.kind === "edit") {
    if (editDetail.isLoading || (editDetail.isPending && !editDetail.data)) {
      return (
        <Card aria-label="커스텀 회사 편집 로딩">
          <CardContent className="py-6 text-center text-sm text-[var(--color-muted)]">
            "{editor.slug}" 회사 전략을 불러오는 중…
          </CardContent>
        </Card>
      );
    }
    if (editDetail.isError || !editDetail.data) {
      return (
        <Card aria-label="커스텀 회사 편집 오류">
          <CardContent className="flex flex-col items-center gap-3 py-6 text-center text-sm">
            <p className="text-[var(--color-fg)]">
              {editDetail.error instanceof Error
                ? editDetail.error.message
                : "회사 상세를 불러오지 못했습니다."}
            </p>
            <Button type="button" size="sm" variant="outline" onClick={() => setEditor(null)}>
              목록으로
            </Button>
          </CardContent>
        </Card>
      );
    }
    return (
      <CustomOperatorForm
        mode="edit"
        initial={editDetail.data}
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
                    onClick={() => setEditor({ kind: "edit", slug: operator.slug })}
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
