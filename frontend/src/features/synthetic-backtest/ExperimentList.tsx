import { useQuery } from "@tanstack/react-query";
import { fetchExperiments } from "@/shared/api";
import {
  Badge,
  Card,
  CardContent,
  CardHeader,
  CardTitle
} from "@/shared/components/ui";
import { experimentRunTone } from "@/shared/constants/statusTone";
import { formatDateTime, formatPercent } from "@/shared/lib";
import type {
  SyntheticExperimentResponse,
  SyntheticExperimentRunSummary
} from "@/shared/types/synthetic";

export interface ExperimentListProps {
  /** 운영자 세션 토큰. */
  token?: string | null;
  selectedId?: number | null;
  onSelect?: (experiment: SyntheticExperimentResponse) => void;
}

const STATUS_LABEL: Record<string, string> = {
  queued: "대기",
  running: "실행 중",
  completed: "완료",
  failed: "실패"
};

function latestRun(
  experiment: SyntheticExperimentResponse
): SyntheticExperimentRunSummary | null {
  const runs = experiment.runs ?? [];
  if (runs.length === 0) return null;
  return [...runs].sort((a, b) => b.id - a.id)[0];
}

function summaryWinRate(run: SyntheticExperimentRunSummary | null): number | null {
  if (!run?.summary) return null;
  const candidate =
    run.summary["average_win_rate_on_settled"] ??
    run.summary["average_win_rate"] ??
    run.summary["win_rate"];
  return typeof candidate === "number" ? candidate : null;
}

function formatCreatedAt(value?: string | null): string {
  if (!value) return "";
  return formatDateTime(value);
}

export function ExperimentList({ token, selectedId, onSelect }: ExperimentListProps) {
  const experiments = useQuery({
    queryKey: ["synthetic", "experiments"],
    queryFn: () => fetchExperiments(token),
    enabled: Boolean(token)
  });

  return (
    <Card aria-label="실험 이력">
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle>
          실험 이력
          <span className="ml-2 text-xs font-normal text-[var(--color-muted)]">
            {experiments.data ? `(${experiments.data.length}건)` : ""}
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        {experiments.isLoading ? (
          <p className="text-xs text-[var(--color-muted)]">불러오는 중…</p>
        ) : null}
        {experiments.data && experiments.data.length === 0 ? (
          <p className="text-xs text-[var(--color-muted)]">
            저장된 실험이 없습니다. 위에서 새 실험을 생성하세요.
          </p>
        ) : null}
        <ul className="flex flex-col gap-2" aria-label="실험 목록">
          {experiments.data?.map((experiment) => {
            const run = latestRun(experiment);
            const winRate = summaryWinRate(run);
            const isSelected = experiment.id === selectedId;
            return (
              <li key={experiment.id}>
                <button
                  type="button"
                  aria-pressed={isSelected}
                  onClick={() => onSelect?.(experiment)}
                  className={`flex w-full flex-col gap-1 rounded-md border px-3 py-2 text-left text-xs transition-colors ${
                    isSelected
                      ? "border-[var(--color-primary)] bg-[var(--color-secondary)]"
                      : "border-[var(--color-border)] bg-[var(--color-card)] hover:bg-[var(--color-secondary)]"
                  }`}
                >
                  <span className="flex w-full items-center justify-between gap-2">
                    <span className="truncate font-medium text-[var(--color-fg)]">
                      {experiment.name}
                    </span>
                    {run ? (
                      <Badge tone={experimentRunTone(run.status)}>
                        {STATUS_LABEL[run.status] ?? run.status}
                      </Badge>
                    ) : (
                      <Badge tone="muted">미실행</Badge>
                    )}
                  </span>
                  <span className="flex w-full items-center justify-between gap-2 text-[var(--color-muted)]">
                    <span>{formatCreatedAt(experiment.created_at)}</span>
                    {winRate != null ? <span>추정 승률 {formatPercent(winRate)}</span> : null}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </CardContent>
    </Card>
  );
}
