import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  fetchDecisionExperimentDetail,
  fetchReevaluationStatus,
  queueDecisionExperimentReevaluation
} from "@/shared/api";
import { Badge, Button, Card, CardContent, CardHeader, CardTitle, toastApi } from "@/shared/components/ui";
import { formatDateTime, formatPercent } from "@/shared/lib";
import type { AuthSession } from "@/app/layout/AuthGate";
import type {
  DecisionExperimentEvaluation,
  ExperimentOutcome
} from "@/shared/types/experiments";

const OUTCOME_TONE: Record<ExperimentOutcome, "info" | "healthy" | "watch" | "critical"> = {
  insufficient_data: "info",
  watch: "watch",
  success: "healthy",
  rollback: "critical",
  inconclusive: "info"
};

const POLL_INTERVAL_MS = 2_000;

export function ExperimentDetailPanel({
  runId,
  session
}: {
  runId: number;
  session: AuthSession | null;
}) {
  const detail = useQuery({
    queryKey: ["experiments", "detail", runId],
    queryFn: () => fetchDecisionExperimentDetail(runId, session?.token),
    enabled: Boolean(session?.token)
  });

  const [taskId, setTaskId] = useState<string | null>(null);
  const taskStatus = useQuery({
    queryKey: ["experiments", "reeval-task", taskId],
    queryFn: () =>
      taskId ? fetchReevaluationStatus(taskId, session?.token) : Promise.resolve(null),
    enabled: Boolean(taskId && session?.token),
    refetchInterval: (query) => {
      const data = query.state.data as { ready?: boolean } | null | undefined;
      return data?.ready ? false : POLL_INTERVAL_MS;
    }
  });

  useEffect(() => {
    if (!taskStatus.data) return;
    if (taskStatus.data.status === "completed") {
      toastApi.success({ title: "재평가 완료", description: taskStatus.data.detail });
      void detail.refetch();
      setTaskId(null);
    } else if (taskStatus.data.status === "failed" || taskStatus.data.status === "cancelled") {
      toastApi.danger({
        title: "재평가 실패",
        description: taskStatus.data.error ?? taskStatus.data.detail
      });
      setTaskId(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskStatus.data?.status]);

  const queueReeval = async () => {
    try {
      const response = await queueDecisionExperimentReevaluation(runId, session?.token);
      setTaskId(response.task_id);
      toastApi.info({ title: "재평가 큐잉", description: response.detail });
    } catch (err) {
      toastApi.danger({
        title: "재평가 큐잉 실패",
        description: err instanceof Error ? err.message : "알 수 없는 오류"
      });
    }
  };

  if (detail.isPending && !detail.data) {
    return <p className="text-xs text-[var(--color-muted)]">상세를 불러오는 중…</p>;
  }
  if (detail.error) {
    return (
      <p
        role="alert"
        className="rounded-md border border-[var(--color-danger)] bg-[color-mix(in_oklch,var(--color-danger),white_85%)] px-2 py-1 text-xs text-[var(--color-danger)]"
      >
        {detail.error.message ?? "상세를 불러오지 못했습니다."}
      </p>
    );
  }
  if (!detail.data) return null;

  const evaluation = detail.data.run.latest_evaluation;
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle className="text-sm">최근 평가</CardTitle>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={queueReeval}
          disabled={Boolean(taskId)}
        >
          {taskId ? `재평가 ${taskStatus.data?.status ?? "queued"}` : "재평가 큐잉"}
        </Button>
      </CardHeader>
      <CardContent className="flex flex-col gap-2 text-xs">
        {evaluation ? (
          <EvaluationSummary evaluation={evaluation} />
        ) : (
          <p className="text-[var(--color-muted)]">
            아직 평가 결과가 없습니다. "재평가 큐잉"으로 백엔드 평가를 트리거하세요.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function EvaluationSummary({ evaluation }: { evaluation: DecisionExperimentEvaluation }) {
  return (
    <>
      <div className="flex items-center justify-between gap-2">
        <span className="text-[var(--color-muted)]">{formatDateTime(evaluation.evaluated_at)}</span>
        <Badge tone={OUTCOME_TONE[evaluation.outcome]}>{evaluation.outcome}</Badge>
      </div>
      <p className="text-[var(--color-fg)]">{evaluation.summary}</p>
      <div className="grid grid-cols-2 gap-2 text-[var(--color-muted)]">
        <Stat
          label={evaluation.target_metric}
          baseline={evaluation.baseline_target_value}
          current={evaluation.current_target_value}
          delta={evaluation.target_delta}
        />
        <Stat
          label={evaluation.guardrail_metric}
          baseline={evaluation.baseline_guardrail_value}
          current={evaluation.current_guardrail_value}
          delta={evaluation.guardrail_delta}
        />
      </div>
      <p className="text-[var(--color-muted)]">
        sample {evaluation.sample_size} · {evaluation.minimum_sample_reached ? "minimum 충족" : "수집 중"} · 권장 액션: {evaluation.recommended_action}
      </p>
    </>
  );
}

function Stat({
  label,
  baseline,
  current,
  delta
}: {
  label: string;
  baseline?: number | null;
  current?: number | null;
  delta?: number | null;
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <span>{label}</span>
      <strong className="tabular-nums text-[var(--color-fg)]">
        {formatPercent(baseline)} → {formatPercent(current)}
        {typeof delta === "number" ? (
          <span className="ml-1 text-[var(--color-muted)]">({delta >= 0 ? "+" : ""}{formatPercent(delta)})</span>
        ) : null}
      </strong>
    </div>
  );
}
