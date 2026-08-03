import { useNavigate } from "react-router-dom";
import { RefreshCw } from "lucide-react";
import { useShellContext } from "@/app/dashboardContext";
import { Badge, Button, Card, CardContent, CardHeader, CardTitle, toastApi } from "@/shared/components/ui";
import { formatCurrencyCompact, formatPercent } from "@/shared/lib";
import { useRefreshEmbeddingMutation, useSimilarProjectsQuery } from "./hooks";

const SIMILAR_LIMIT = 5;

export function SimilarPanel({ projectId }: { projectId: number }) {
  const { session } = useShellContext();
  const navigate = useNavigate();
  const similar = useSimilarProjectsQuery(session, projectId, { limit: SIMILAR_LIMIT });
  const refresh = useRefreshEmbeddingMutation(session);

  const embeddingStatus = similar.data?.target_embedding_status;
  const hasEmbedding = embeddingStatus ? embeddingStatus === "ready" : Boolean(similar.data?.target_embedding_model);
  const handleRefresh = async () => {
    try {
      const result = await refresh.mutateAsync({ id: projectId, force: true });
      toastApi.success({
        title: "임베딩 재계산 요청됨",
        description: `${result.queue} 큐에 작업이 등록되었습니다.`
      });
    } catch (err) {
      toastApi.danger({
        title: "임베딩 재계산 실패",
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
          disabled={refresh.isPending}
          aria-label="임베딩 재계산"
        >
          <RefreshCw size={14} className="mr-1" />
          {refresh.isPending ? "재계산 중" : "재계산"}
        </Button>
      </CardHeader>
      <CardContent className="flex flex-col gap-2 text-xs">
        {!hasEmbedding && similar.data ? (
          <p className="rounded-md border border-[var(--color-warn)] bg-[color-mix(in_oklch,var(--color-warn),white_85%)] px-2 py-1 text-[var(--color-warn)]">
            이 공고의 임베딩이 아직 생성되지 않았습니다. "재계산"으로 다시 시도하세요.
          </p>
        ) : null}
        {similar.error ? (
          <p className="text-[var(--color-danger)]" role="alert">
            {similar.error.message ?? "유사 공고를 불러오지 못했습니다."}
          </p>
        ) : null}
        {similar.isPending && !similar.data ? (
          <p className="text-[var(--color-muted)]">불러오는 중…</p>
        ) : null}
        {similar.data && similar.data.results.length === 0 ? (
          <p className="text-[var(--color-muted)]">유사한 공고가 없습니다.</p>
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
