import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { useShellContext } from "@/app/dashboardContext";
import { fetchOperationsDashboard } from "@/shared/api";
import { Badge, Button, Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import { formatDateTime, formatPercent } from "@/shared/lib";
import type {
  OperationsCardStatus,
  OperationsDashboardCard,
  OperationsDashboardResponse
} from "@/shared/types/operations";

const REFRESH_INTERVAL_MS = 30_000;

const TONE: Record<OperationsCardStatus, "info" | "healthy" | "watch" | "critical"> = {
  info: "info",
  healthy: "healthy",
  watch: "watch",
  critical: "critical"
};

export function OperationsScreen() {
  const { session } = useShellContext();
  const operations = useQuery<OperationsDashboardResponse, Error>({
    queryKey: ["operations", "dashboard"],
    queryFn: () => fetchOperationsDashboard({ days: 7 }, session?.token),
    enabled: Boolean(session?.token),
    refetchInterval: (query) => (document.visibilityState === "visible" ? REFRESH_INTERVAL_MS : false),
    refetchIntervalInBackground: false
  });

  const criticalCards =
    operations.data?.cards.filter((card) => card.status === "critical") ?? [];

  return (
    <section className="flex flex-col gap-4" aria-label="운영 대시보드">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg font-semibold text-[var(--color-fg)]">운영 대시보드</h2>
        <div className="flex items-center gap-2 text-xs">
          <span className="text-[var(--color-muted)]">
            30초마다 자동 갱신 (탭 비활성 시 정지)
          </span>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => operations.refetch()}
            disabled={operations.isFetching}
            aria-label="새로고침"
          >
            <RefreshCw size={14} />
          </Button>
        </div>
      </header>

      {criticalCards.length > 0 ? (
        <div
          role="alert"
          aria-label="인시던트 알림"
          className="rounded-md border border-[var(--color-danger)] bg-[color-mix(in_oklch,var(--color-danger),white_85%)] px-3 py-2 text-sm text-[var(--color-danger)]"
        >
          <strong>인시던트 {criticalCards.length}건:</strong>{" "}
          {criticalCards.map((card) => card.label).join(", ")}
        </div>
      ) : null}

      {operations.error ? (
        <p
          role="alert"
          className="rounded-md border border-[var(--color-danger)] bg-[color-mix(in_oklch,var(--color-danger),white_85%)] px-3 py-2 text-sm text-[var(--color-danger)]"
        >
          {operations.error.message ?? "운영 대시보드를 불러오지 못했습니다."}
        </p>
      ) : null}

      {operations.isPending && !operations.data ? (
        <p className="text-sm text-[var(--color-muted)]">불러오는 중…</p>
      ) : null}

      {operations.data ? (
        <>
          <section
            className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3"
            aria-label="요약 카드"
          >
            {operations.data.cards.map((card) => (
              <SummaryCard key={card.key} card={card} />
            ))}
          </section>

          <CrawlHealth summary={operations.data.crawl} />
          <TelegramHealth summary={operations.data.notifications} />
          <MlReleaseCard summary={operations.data.ml_release} />
        </>
      ) : null}
    </section>
  );
}

function SummaryCard({ card }: { card: OperationsDashboardCard }) {
  const valueLabel =
    card.unit === "ratio" ? formatPercent(card.value) : card.value.toLocaleString("ko-KR");
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle className="text-sm">{card.label}</CardTitle>
        <Badge tone={TONE[card.status]}>{card.status}</Badge>
      </CardHeader>
      <CardContent className="flex flex-col gap-1">
        <strong className="text-2xl tabular-nums text-[var(--color-fg)]">{valueLabel}</strong>
        <span className="text-xs text-[var(--color-muted)]">{card.detail}</span>
      </CardContent>
    </Card>
  );
}

function CrawlHealth({ summary }: { summary: OperationsDashboardResponse["crawl"] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>크롤 상태</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-2 text-xs">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <Stat label="job_count" value={summary.job_count.toString()} />
          <Stat label="completed" value={summary.completed_count.toString()} />
          <Stat label="failed" value={summary.failed_count.toString()} />
          <Stat label="success_rate" value={formatPercent(summary.success_rate)} />
        </div>
        <p className="text-[var(--color-muted)]">
          last_success {summary.last_success_at ? formatDateTime(summary.last_success_at) : "-"} ·
          last_failure {summary.last_failure_at ? formatDateTime(summary.last_failure_at) : "-"}
        </p>
        {summary.recent_failures.length > 0 ? (
          <details>
            <summary className="cursor-pointer text-[var(--color-fg)]">최근 실패 ({summary.recent_failures.length})</summary>
            <ul className="mt-1 flex flex-col gap-1">
              {summary.recent_failures.slice(0, 5).map((failure) => (
                <li key={failure.crawl_job_id} className="text-[var(--color-danger)]">
                  {failure.source} · {failure.error_message ?? failure.status}
                </li>
              ))}
            </ul>
          </details>
        ) : null}
      </CardContent>
    </Card>
  );
}

function TelegramHealth({ summary }: { summary: OperationsDashboardResponse["notifications"] }) {
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle>텔레그램 / 알림</CardTitle>
        <Badge tone={TONE[summary.telegram_status]}>{summary.telegram_status}</Badge>
      </CardHeader>
      <CardContent className="flex flex-col gap-2 text-xs">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <Stat label="전송 시도" value={summary.telegram_delivery_attempt_count.toString()} />
          <Stat label="성공" value={summary.telegram_sent_count.toString()} />
          <Stat label="실패" value={summary.telegram_failed_count.toString()} />
          <Stat label="성공률" value={formatPercent(summary.telegram_success_rate)} />
        </div>
        <p className="text-[var(--color-muted)]">{summary.telegram_detail}</p>
      </CardContent>
    </Card>
  );
}

function MlReleaseCard({ summary }: { summary: OperationsDashboardResponse["ml_release"] }) {
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle>ML release</CardTitle>
        <Badge tone={TONE[summary.status]}>{summary.status}</Badge>
      </CardHeader>
      <CardContent className="flex flex-col gap-2 text-xs">
        <p className="text-[var(--color-fg)]">{summary.detail}</p>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          <Stat label="latest_tag" value={summary.latest_release_tag ?? "-"} />
          <Stat label="signature" value={summary.latest_signature_status} />
          <Stat label="gate" value={summary.latest_gate_status} />
        </div>
        <p className="text-[var(--color-muted)]">backtest: {summary.backtest_detail}</p>
      </CardContent>
    </Card>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[var(--color-muted)]">{label}</span>
      <strong className="tabular-nums text-[var(--color-fg)]">{value}</strong>
    </div>
  );
}
