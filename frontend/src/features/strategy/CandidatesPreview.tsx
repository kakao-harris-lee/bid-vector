import { useState } from "react";
import { Badge, Button, Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import { useShellContext } from "@/app/dashboardContext";
import { formatCurrencyCompact } from "@/shared/lib";
import { EligibilityFeedbackButtons } from "./EligibilityFeedbackButtons";
import { useStrategyCandidatesQuery } from "./hooks";

const CANDIDATE_LIMIT = 5;

export function CandidatesPreview() {
  const { session } = useShellContext();
  const [highPriorityOnly, setHighPriorityOnly] = useState(false);
  const query = useStrategyCandidatesQuery(session, {
    limit: CANDIDATE_LIMIT,
    highPriorityOnly
  });

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle>영향 후보 미리보기</CardTitle>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => query.refetch()}
          disabled={query.isFetching}
        >
          {query.isFetching ? "갱신 중" : "새로고침"}
        </Button>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <label className="flex cursor-pointer items-center gap-2 text-xs">
          <input
            type="checkbox"
            checked={highPriorityOnly}
            onChange={(event) => setHighPriorityOnly(event.target.checked)}
            className="h-4 w-4 accent-[var(--color-primary)]"
          />
          <span>우선순위 높음만</span>
        </label>
        {query.error ? (
          <p className="text-xs text-[var(--color-danger)]" role="alert">
            {query.error.message ?? "후보를 불러오지 못했습니다."}
          </p>
        ) : null}
        {query.data ? (
          <div className="flex flex-col gap-2">
            <div className="flex items-baseline justify-between text-xs">
              <span className="text-[var(--color-muted)]">평가</span>
              <strong className="tabular-nums">{query.data.evaluated_project_count}건</strong>
            </div>
            <div className="flex items-baseline justify-between text-xs">
              <span className="text-[var(--color-muted)]">매칭 후보</span>
              <strong className="tabular-nums">{query.data.returned_candidate_count}건</strong>
            </div>
            <ul className="flex flex-col gap-2 pt-2" aria-label="상위 후보">
              {query.data.candidates.length === 0 ? (
                <li className="text-xs text-[var(--color-muted)]">현재 매칭되는 후보가 없습니다.</li>
              ) : (
                query.data.candidates.map((candidate) => (
                  <li
                    key={candidate.project_id}
                    className="flex flex-col gap-1 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] p-2 text-xs"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate font-medium text-[var(--color-fg)]" title={candidate.title}>
                        {candidate.title}
                      </span>
                      <Badge tone={toneForAction(candidate.action)}>{labelAction(candidate.action)}</Badge>
                    </div>
                    <div className="flex items-center justify-between text-[var(--color-muted)]">
                      <span>{candidate.category ?? "-"}</span>
                      <span className="tabular-nums">
                        {formatCurrencyCompact(candidate.budget_estimate)}
                      </span>
                    </div>
                    <EligibilityFeedbackButtons
                      projectId={candidate.project_id}
                      session={session}
                    />
                  </li>
                ))
              )}
            </ul>
          </div>
        ) : query.isPending ? (
          <p className="text-xs text-[var(--color-muted)]">불러오는 중…</p>
        ) : null}
      </CardContent>
    </Card>
  );
}

function toneForAction(action: "bid_now" | "review" | "skip"): "healthy" | "watch" | "muted" {
  if (action === "bid_now") return "healthy";
  if (action === "review") return "watch";
  return "muted";
}

function labelAction(action: "bid_now" | "review" | "skip"): string {
  if (action === "bid_now") return "투찰";
  if (action === "review") return "검토";
  return "보류";
}
