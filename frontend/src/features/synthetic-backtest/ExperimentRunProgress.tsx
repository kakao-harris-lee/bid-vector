import { useQuery } from "@tanstack/react-query";
import { experimentRunCsvUrl, fetchExperimentRun } from "@/shared/api";
import { Badge, type BadgeTone, Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import type { SyntheticExperimentRunResponse } from "@/shared/types/synthetic";
import { Leaderboard } from "./Leaderboard";
import { BreakdownView } from "./BreakdownView";

export interface ExperimentRunProgressProps {
  experimentId: number;
  runId: number;
  /** 운영자 세션 토큰. */
  token?: string | null;
  /** 폴링 주기(ms). 기본 1500. */
  pollIntervalMs?: number;
}

const TERMINAL_STATUSES = new Set(["completed", "failed"]);

function isTerminal(status: string | undefined): boolean {
  return status != null && TERMINAL_STATUSES.has(status);
}

function statusTone(status: string | undefined): BadgeTone {
  if (status === "completed") return "healthy";
  if (status === "failed") return "critical";
  if (status === "running") return "info";
  return "muted";
}

function progressLabel(run: SyntheticExperimentRunResponse | undefined): string {
  if (!run?.summary) return "실험 실행을 준비하고 있습니다…";
  const processed = run.summary["processed_count"] ?? run.summary["completed_count"];
  const total = run.summary["operator_count"] ?? run.summary["total_count"];
  if (typeof processed === "number" && typeof total === "number") {
    return `${processed}/${total} 회사 처리 중…`;
  }
  return "실험을 실행하고 있습니다…";
}

export function ExperimentRunProgress({
  experimentId,
  runId,
  token,
  pollIntervalMs = 1500
}: ExperimentRunProgressProps) {
  const run = useQuery({
    queryKey: ["synthetic", "experiments", experimentId, "runs", runId],
    queryFn: () => fetchExperimentRun(experimentId, runId, token),
    refetchInterval: (query) =>
      isTerminal(query.state.data?.status) ? false : pollIntervalMs
  });

  const data = run.data;
  const status = data?.status;
  const results = data?.results ?? [];

  return (
    <Card aria-label="실험 실행 진행">
      <CardHeader className="flex-row items-center justify-between gap-2">
        <CardTitle>실행 진행 / 결과</CardTitle>
        <div className="flex items-center gap-2">
          {status === "completed" ? (
            <a
              href={experimentRunCsvUrl(experimentId, runId)}
              download
              className="inline-flex h-8 items-center rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-2.5 text-xs font-medium text-[var(--color-fg)] transition-colors hover:bg-[var(--color-secondary)]"
              aria-label="런 결과 CSV 다운로드"
            >
              CSV 다운로드
            </a>
          ) : null}
          {status ? <Badge tone={statusTone(status)}>{status}</Badge> : null}
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 text-xs">
        {run.isLoading ? (
          <p className="text-[var(--color-muted)]">상태 조회 중…</p>
        ) : null}

        {data && (status === "queued" || status === "running") ? (
          <p className="text-[var(--color-muted)]" role="status">
            <span
              aria-hidden="true"
              className="mr-2 inline-block h-3 w-3 animate-spin rounded-full border-2 border-[var(--color-border)] border-t-[var(--color-primary)] align-middle"
            />
            {progressLabel(data)}
          </p>
        ) : null}

        {data && status === "failed" ? (
          <div
            role="alert"
            className="rounded-md border border-[var(--color-danger)] bg-[color-mix(in_oklch,var(--color-danger),white_80%)] p-3"
          >
            <p className="font-medium text-[color-mix(in_oklch,var(--color-danger),black_30%)]">
              실행 실패
            </p>
            <p className="text-[var(--color-fg)]">
              {data.error ?? "알 수 없는 오류로 실패했습니다."}
            </p>
          </div>
        ) : null}

        {data && status === "completed" ? (
          results.length === 0 ? (
            <p className="py-2 text-center text-[var(--color-muted)]">결과 데이터가 없습니다.</p>
          ) : (
            <div className="flex flex-col gap-4">
              <Leaderboard results={results} />
              <BreakdownView results={results} />
            </div>
          )
        ) : null}
      </CardContent>
    </Card>
  );
}
