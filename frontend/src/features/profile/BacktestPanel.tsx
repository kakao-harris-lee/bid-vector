import { Button, Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import { ApiError } from "@/shared/api";
import { logoutSession } from "@/app/layout/AuthGate";
import { useShellContext } from "@/app/dashboardContext";
import { useProfileBacktestMutation } from "./hooks";
import type {
  PaperBiddingCandidateItem,
  PaperBiddingRunSummary
} from "@/shared/types/profile";

const ACTION_LABELS: Record<string, string> = {
  bid_now: "즉시 투찰",
  review: "검토",
  skip: "보류"
};

function actionLabel(action: string): string {
  return ACTION_LABELS[action] ?? action;
}

/**
 * "이 프로필로 백테스트" — runs an on-demand historical paper-bidding backtest
 * so the operator can immediately see how the just-saved profile changes the
 * matching outcome. `persist=false` keeps it side-effect free.
 */
export function BacktestPanel() {
  const { session } = useShellContext();
  const mutation = useProfileBacktestMutation(session);

  const run = () => {
    mutation.mutate(
      { scenario: "base", limit: 30, persist: false },
      {
        onError: (err) => {
          if (err instanceof ApiError && err.status === 401) {
            logoutSession();
          }
        }
      }
    );
  };

  const data = mutation.data;
  const summary = data?.summary;
  const topItems = (data?.items ?? []).slice(0, 5);

  return (
    <Card>
      <CardHeader className="flex flex-col gap-1">
        <CardTitle>이 프로필로 백테스트</CardTitle>
        <p className="text-xs text-[var(--color-muted)]">
          저장한 면허·지역·예산이 과거 공고 매칭을 어떻게 바꾸는지 즉시 확인합니다.
        </p>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <Button type="button" onClick={run} disabled={mutation.isPending}>
          {mutation.isPending ? "실행 중…" : "백테스트 실행"}
        </Button>

        {mutation.isError ? (
          <p className="text-sm text-[var(--color-danger)]" role="alert">
            {mutation.error instanceof Error
              ? mutation.error.message
              : "백테스트 실행에 실패했습니다."}
          </p>
        ) : null}

        {summary ? <BacktestSummary summary={summary} /> : null}

        {data && topItems.length === 0 ? (
          <p className="text-xs text-[var(--color-muted)]">
            매칭된 후보가 없습니다. 면허·지역·예산 조건을 조정해 보세요.
          </p>
        ) : null}

        {topItems.length ? (
          <ul className="flex flex-col gap-2" aria-label="상위 매칭 후보">
            {topItems.map((item) => (
              <CandidateRow key={item.project_id} item={item} />
            ))}
          </ul>
        ) : null}
      </CardContent>
    </Card>
  );
}

function BacktestSummary({ summary }: { summary: PaperBiddingRunSummary }) {
  const counts = summary.action_counts ?? {};
  return (
    <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-card)] p-3 text-sm">
      <p className="font-medium text-[var(--color-fg)]">
        후보 {summary.candidate_count ?? 0}건
      </p>
      <dl className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-[var(--color-muted)]">
        <span>
          <dt className="inline">즉시 투찰 </dt>
          <dd className="inline tabular-nums text-[var(--color-fg)]">
            {counts.bid_now ?? 0}
          </dd>
        </span>
        <span>
          <dt className="inline">검토 </dt>
          <dd className="inline tabular-nums text-[var(--color-fg)]">
            {counts.review ?? summary.review_count ?? 0}
          </dd>
        </span>
        <span>
          <dt className="inline">보류 </dt>
          <dd className="inline tabular-nums text-[var(--color-fg)]">
            {counts.skip ?? summary.skip_count ?? 0}
          </dd>
        </span>
      </dl>
    </div>
  );
}

function CandidateRow({ item }: { item: PaperBiddingCandidateItem }) {
  const score =
    typeof item.matched_score === "number"
      ? `${(item.matched_score * 100).toFixed(0)}%`
      : "-";
  const reasoning = (item.reasoning ?? "").trim();
  return (
    <li className="rounded-md border border-[var(--color-border)] p-2 text-xs">
      <div className="flex items-center justify-between gap-2">
        <span className="truncate font-medium text-[var(--color-fg)]">
          {item.project_title ?? `공고 #${item.project_id}`}
        </span>
        <span className="shrink-0 rounded-full bg-[var(--color-secondary)] px-2 py-0.5 text-[var(--color-secondary-foreground)]">
          {actionLabel(item.action)}
        </span>
      </div>
      <p className="mt-1 text-[var(--color-muted)]">
        매칭 점수 <span className="tabular-nums text-[var(--color-fg)]">{score}</span>
      </p>
      {reasoning ? (
        <p className="mt-1 line-clamp-2 text-[var(--color-muted)]">{reasoning}</p>
      ) : null}
    </li>
  );
}
