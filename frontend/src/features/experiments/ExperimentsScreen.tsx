import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useShellContext } from "@/app/dashboardContext";
import {
  applyExperimentThresholds,
  fetchDecisionExperiments,
  queryKeys,
  type ApplyThresholdsRequest
} from "@/shared/api";
import { ApiError } from "@/shared/api";
import { Badge, Button, Card, CardContent, CardHeader, CardTitle, toastApi } from "@/shared/components/ui";
import { logoutSession } from "@/app/layout/AuthGate";
import { formatDateTime, formatPercent } from "@/shared/lib";
import type {
  DecisionExperimentRunSummary,
  DecisionExperimentThresholdApplyResponse,
  ExperimentReviewBucket
} from "@/shared/types/experiments";

const BUCKET_LABEL: Record<ExperimentReviewBucket, string> = {
  ready_to_apply: "적용 대기",
  blocked: "차단",
  failed: "실패",
  needs_evaluation: "재평가 필요",
  collecting_data: "데이터 수집",
  partially_applied: "부분 적용",
  scheduled: "예정",
  applied: "적용 완료",
  unsupported: "미지원"
};

const BUCKET_TONE: Record<ExperimentReviewBucket, "info" | "healthy" | "watch" | "critical" | "muted"> = {
  ready_to_apply: "watch",
  blocked: "critical",
  failed: "critical",
  needs_evaluation: "info",
  collecting_data: "info",
  partially_applied: "watch",
  scheduled: "info",
  applied: "healthy",
  unsupported: "muted"
};

export function ExperimentsScreen() {
  const { session } = useShellContext();
  const [sort, setSort] = useState<"needs_attention" | "created_desc" | "priority">(
    "needs_attention"
  );
  const list = useQuery({
    queryKey: queryKeys.experiments.list({ sort, limit: 20 }),
    queryFn: () => fetchDecisionExperiments({ sort, limit: 20 }, session?.token),
    enabled: Boolean(session?.token)
  });

  const [active, setActive] = useState<DecisionExperimentRunSummary | null>(null);

  return (
    <section className="flex flex-col gap-4">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg font-semibold text-[var(--color-fg)]">실험 lifecycle</h2>
        <label className="flex items-center gap-2 text-xs">
          정렬
          <select
            value={sort}
            onChange={(event) => setSort(event.target.value as typeof sort)}
            className="h-8 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-2 text-xs"
          >
            <option value="needs_attention">검토 우선순위</option>
            <option value="created_desc">최신순</option>
            <option value="priority">priority_rank</option>
          </select>
        </label>
      </header>

      {list.error ? (
        <p
          role="alert"
          className="rounded-md border border-[var(--color-danger)] bg-[color-mix(in_oklch,var(--color-danger),white_85%)] px-3 py-2 text-sm text-[var(--color-danger)]"
        >
          {list.error.message ?? "실험 목록을 불러오지 못했습니다."}
        </p>
      ) : null}

      {list.isPending && !list.data ? (
        <p className="text-sm text-[var(--color-muted)]">불러오는 중…</p>
      ) : null}

      {list.data && list.data.runs.length === 0 ? (
        <p className="text-sm text-[var(--color-muted)]">표시할 실험이 없습니다.</p>
      ) : null}

      <ul className="flex flex-col gap-2" aria-label="실험 목록">
        {list.data?.runs.map((run) => (
          <li key={run.id}>
            <button
              type="button"
              onClick={() => setActive(run)}
              className="flex w-full flex-col gap-1 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] p-3 text-left transition-colors hover:border-[var(--color-primary)]"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate font-medium text-[var(--color-fg)]">{run.title}</span>
                <div className="flex items-center gap-1">
                  <Badge tone={BUCKET_TONE[run.review_bucket]}>{BUCKET_LABEL[run.review_bucket]}</Badge>
                  <Badge tone="info">priority {run.priority_rank}</Badge>
                </div>
              </div>
              <p className="text-xs text-[var(--color-muted)]">{run.hypothesis}</p>
              <p className="text-xs text-[var(--color-muted)]">
                target: {run.target_metric} · 마지막 평가{" "}
                {run.last_evaluated_at ? formatDateTime(run.last_evaluated_at) : "-"}
              </p>
            </button>
          </li>
        ))}
      </ul>

      {active ? (
        <ApplyThresholdsDialog
          run={active}
          session={session}
          onClose={() => setActive(null)}
        />
      ) : null}
    </section>
  );
}

function ApplyThresholdsDialog({
  run,
  session,
  onClose
}: {
  run: DecisionExperimentRunSummary;
  session: ReturnType<typeof useShellContext>["session"];
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [dryRunResult, setDryRunResult] = useState<DecisionExperimentThresholdApplyResponse | null>(
    null
  );

  const mutation = useMutation<
    DecisionExperimentThresholdApplyResponse,
    Error,
    ApplyThresholdsRequest
  >({
    mutationFn: (payload) => applyExperimentThresholds(run.id, payload, session?.token),
    onSuccess: (data, variables) => {
      if (variables.dry_run) {
        setDryRunResult(data);
        toastApi.info({
          title: "Dry-run 결과 확인",
          description: data.detail
        });
        return;
      }
      toastApi.success({
        title: "임계값 적용 완료",
        description: data.detail
      });
      void queryClient.invalidateQueries({ queryKey: ["experiments", "list"] });
      void queryClient.invalidateQueries({ queryKey: ["strategy", "detail"] });
      onClose();
    },
    onError: (err) => {
      if (err instanceof ApiError && err.status === 401) {
        logoutSession();
        onClose();
        return;
      }
      toastApi.danger({
        title: "임계값 적용 실패",
        description: err.message ?? "다음 액션: 결정 데이터 확인 후 재시도"
      });
    }
  });

  const hasThresholdsSupport = run.supported_apply_types.includes("thresholds");
  const ready = dryRunResult !== null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="apply-thresholds-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
    >
      <Card className="w-full max-w-lg">
        <CardHeader>
          <CardTitle id="apply-thresholds-title">임계값 적용 — {run.title}</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3 text-sm">
          <p className="text-[var(--color-muted)]">
            {hasThresholdsSupport
              ? "Dry-run으로 변경 diff를 확인한 뒤 force 적용하세요."
              : "이 실험은 임계값 적용을 지원하지 않습니다."}
          </p>

          {dryRunResult ? (
            <div className="rounded-md border border-[var(--color-border)] bg-[color-mix(in_oklch,var(--color-info),white_92%)] p-2 text-xs">
              <p className="font-medium text-[var(--color-fg)]">Dry-run diff</p>
              <ul className="mt-1 flex flex-col gap-1">
                {dryRunResult.threshold_updates.map((item) => (
                  <li key={item.parameter} className="tabular-nums">
                    {item.label}: {formatPercent(item.previous_value)} →{" "}
                    {formatPercent(item.suggested_value)} ({item.direction === "increase" ? "+" : "-"}
                    {formatPercent(Math.abs(item.delta))})
                  </li>
                ))}
              </ul>
              <p className="mt-1 text-[var(--color-muted)]">{dryRunResult.detail}</p>
            </div>
          ) : null}

          <div className="flex items-center justify-end gap-2">
            <Button type="button" variant="outline" size="sm" onClick={onClose} disabled={mutation.isPending}>
              닫기
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => mutation.mutate({ dry_run: true })}
              disabled={!hasThresholdsSupport || mutation.isPending}
            >
              {mutation.isPending ? "확인 중" : "Dry-run"}
            </Button>
            <Button
              type="button"
              size="sm"
              onClick={() => mutation.mutate({ force: true })}
              disabled={!hasThresholdsSupport || !ready || mutation.isPending}
              aria-disabled={!hasThresholdsSupport || !ready || mutation.isPending}
            >
              {mutation.isPending ? "적용 중" : "Force 적용"}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
