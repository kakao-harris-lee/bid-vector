import { Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import { LabeledStat } from "@/shared/components";
import { formatDateTime, formatPercent } from "@/shared/lib";
import type { OperationsDashboardResponse } from "@/shared/types/operations";

export function CrawlHealth({ summary }: { summary: OperationsDashboardResponse["crawl"] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>크롤 상태</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-2 text-xs">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <LabeledStat label="job_count" value={summary.job_count.toString()} />
          <LabeledStat label="completed" value={summary.completed_count.toString()} />
          <LabeledStat label="failed" value={summary.failed_count.toString()} />
          <LabeledStat label="success_rate" value={formatPercent(summary.success_rate)} />
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
