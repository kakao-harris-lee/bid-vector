import { Badge, Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import { useShellContext } from "@/app/dashboardContext";
import { formatDateTime } from "@/shared/lib";
import { useStrategyRunsQuery } from "./hooks";
import type { OperatorStrategyRunItem, StrategyRunStatus } from "@/shared/types/strategy";

export function RecentRuns() {
  const { session } = useShellContext();
  const query = useStrategyRunsQuery(session, 5);

  return (
    <Card>
      <CardHeader>
        <CardTitle>최근 모니터링</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        {query.isPending && !query.data ? (
          <p className="text-xs text-[var(--color-muted)]">불러오는 중…</p>
        ) : null}
        {query.error ? (
          <p className="text-xs text-[var(--color-danger)]" role="alert">
            {query.error.message ?? "이력을 불러오지 못했습니다."}
          </p>
        ) : null}
        {query.data && query.data.runs.length === 0 ? (
          <p className="text-xs text-[var(--color-muted)]">아직 모니터링 실행 기록이 없습니다.</p>
        ) : null}
        {query.data?.runs.map((run) => <RunRow key={run.id} run={run} />)}
      </CardContent>
    </Card>
  );
}

function RunRow({ run }: { run: OperatorStrategyRunItem }) {
  return (
    <div className="flex flex-col gap-1 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] p-2 text-xs">
      <div className="flex items-center justify-between">
        <span className="text-[var(--color-muted)]">{formatDateTime(run.created_at)}</span>
        <Badge tone={toneForRunStatus(run.status)}>{labelRunStatus(run.status)}</Badge>
      </div>
      <div className="flex items-center justify-between tabular-nums">
        <span className="text-[var(--color-muted)]">{labelTrigger(run.trigger_source)}</span>
        <span>
          후보 {run.persisted_candidate_count}건 · 알림 {run.notification_count}건
        </span>
      </div>
      {run.error_message ? (
        <p className="text-[var(--color-danger)]">{run.error_message}</p>
      ) : null}
    </div>
  );
}

function toneForRunStatus(status: StrategyRunStatus): "info" | "healthy" | "watch" | "critical" {
  if (status === "completed") return "healthy";
  if (status === "failed") return "critical";
  if (status === "running" || status === "queued") return "watch";
  if (status === "cancelled") return "info";
  return "info";
}

function labelRunStatus(status: StrategyRunStatus): string {
  const map: Record<StrategyRunStatus, string> = {
    queued: "대기",
    running: "실행 중",
    completed: "완료",
    failed: "실패",
    cancelled: "취소"
  };
  return map[status];
}

function labelTrigger(source: string): string {
  if (source === "sync") return "동기 실행";
  if (source === "async") return "비동기 실행";
  if (source === "schedule") return "스케줄";
  return source;
}
