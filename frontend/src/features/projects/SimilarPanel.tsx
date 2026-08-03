import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { RefreshCw } from "lucide-react";
import { useShellContext } from "@/app/dashboardContext";
import { Badge, Button, Card, CardContent, CardHeader, CardTitle, toastApi } from "@/shared/components/ui";
import { formatCurrencyCompact, formatPercent } from "@/shared/lib";
import type { SimilarProjectsRefreshOperationResponse } from "@/shared/types/project";
import {
  useRefreshSimilarProjectsMutation,
  useSimilarProjectsQuery,
  useSimilarProjectsRefreshStatusQuery
} from "./hooks";

const SIMILAR_LIMIT = 5;

export function SimilarPanel({ projectId }: { projectId: number }) {
  const { session } = useShellContext();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [refreshOperation, setRefreshOperation] =
    useState<SimilarProjectsRefreshOperationResponse | null>(null);
  const similar = useSimilarProjectsQuery(session, projectId, { limit: SIMILAR_LIMIT });
  const refresh = useRefreshSimilarProjectsMutation(session);
  const refreshStatus = useSimilarProjectsRefreshStatusQuery(session, refreshOperation);

  useEffect(() => {
    const result = refreshStatus.data;
    if (!refreshOperation || !result?.is_terminal) return;
    if (result.succeeded) {
      void queryClient.invalidateQueries({ queryKey: ["projects", "similar", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["projects", "detail", projectId] });
      toastApi.success({
        title: "유사 공고 갱신 완료",
        description: "최신 유사 공고 결과를 불러옵니다."
      });
    } else {
      toastApi.danger({
        title: "유사 공고 갱신 실패",
        description: result.error || result.message
      });
    }
    setRefreshOperation(null);
  }, [projectId, queryClient, refreshOperation, refreshStatus.data]);

  useEffect(() => {
    if (!refreshOperation || !refreshStatus.error) return;
    toastApi.danger({
      title: "유사 공고 갱신 상태 확인 실패",
      description: refreshStatus.error.message
    });
    setRefreshOperation(null);
  }, [refreshOperation, refreshStatus.error]);

  const handleRefresh = async () => {
    try {
      const result = await refresh.mutateAsync({ id: projectId, force: true });
      setRefreshOperation(result);
      toastApi.success({
        title: "유사 공고 갱신 요청됨",
        description: result.message
      });
    } catch (err) {
      toastApi.danger({
        title: "유사 공고 갱신 실패",
        description: err instanceof Error ? err.message : "알 수 없는 오류"
      });
    }
  };

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle>유사 공고</CardTitle>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={handleRefresh}
          disabled={refresh.isPending || refreshOperation !== null}
          aria-label="유사 공고 갱신"
        >
          <RefreshCw size={14} className="mr-1" />
          {refresh.isPending || refreshOperation ? "갱신 중" : "갱신"}
        </Button>
      </CardHeader>
      <CardContent className="flex flex-col gap-2 text-xs">
        {similar.error ? (
          <p className="text-[var(--color-danger)]" role="alert">
            {similar.error.message ?? "유사 공고를 불러오지 못했습니다."}
          </p>
        ) : null}
        {similar.isPending && !similar.data ? (
          <p className="text-[var(--color-muted)]">불러오는 중…</p>
        ) : null}
        {similar.data && similar.data.results.length === 0 ? (
          <p className="text-[var(--color-muted)]">
            유사한 공고가 없습니다. 갱신하면 최신 결과를 확인할 수 있습니다.
          </p>
        ) : null}
        {similar.data?.results.length ? (
          <ul className="flex flex-col gap-2" aria-label="유사 공고 결과">
            {similar.data.results.map((item) => (
              <li key={item.project_id}>
                <button
                  type="button"
                  className="flex w-full flex-col gap-1 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] p-2 text-left transition-colors hover:border-[var(--color-primary)]"
                  onClick={() => navigate(`/dashboard/projects/${item.project_id}`)}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate font-medium text-[var(--color-fg)]" title={item.title}>
                      {item.title}
                    </span>
                    <Badge tone="info">{formatPercent(item.similarity_score)}</Badge>
                  </div>
                  <div className="flex items-center justify-between text-[var(--color-muted)]">
                    <span>{item.category ?? "-"}</span>
                    <span className="tabular-nums">{formatCurrencyCompact(item.budget_estimate)}</span>
                  </div>
                </button>
              </li>
            ))}
          </ul>
        ) : null}
      </CardContent>
    </Card>
  );
}
