import { Badge, Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import { LabeledStat } from "@/shared/components";
import { formatDateTime } from "@/shared/lib";
import type { OperationsDashboardResponse } from "@/shared/types/operations";
import { TONE, runStatusTone, sampleStatusLabel } from "./helpers";

export function SyntheticValidationCard({
  summary
}: {
  summary: OperationsDashboardResponse["synthetic_validation"];
}) {
  const latest = summary.latest ?? null;
  const presets = summary.presets ?? [];
  return (
    <Card aria-label="G-1 가상 회사 검증">
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-2">
        <CardTitle className="break-words leading-snug">G-1 가상 회사 검증</CardTitle>
        <Badge className="shrink-0 whitespace-nowrap" tone={TONE[summary.status]}>
          {summary.status}
        </Badge>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 text-xs">
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-4">
          <LabeledStat
            label="preset 저장"
            value={`${summary.saved_preset_count}/${summary.preset_count}`}
          />
          <LabeledStat
            label="완료 preset"
            value={`${summary.completed_preset_count}/${summary.preset_count}`}
          />
          <LabeledStat
            label="충분 표본"
            value={`${summary.sufficient_preset_count}/${summary.preset_count}`}
          />
          <LabeledStat
            label="최근 실행"
            value={`${summary.recent_completed_count}/${summary.recent_run_count}`}
          />
        </div>

        <p className="break-words text-[var(--color-muted)]">{summary.detail}</p>

        {latest ? (
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <span className="text-[var(--color-muted)]">최근 run</span>
            <span className="break-words text-[var(--color-fg)]">
              {latest.experiment_name ?? `experiment ${latest.experiment_id}`}
            </span>
            <Badge className="shrink-0 whitespace-nowrap" tone={runStatusTone(latest.status)}>
              {latest.status}
            </Badge>
            <span className="break-words text-[var(--color-fg)]">
              settled {latest.total_settled_count}/{summary.sample_target}
            </span>
            <span className="break-words text-[var(--color-muted)]">
              {latest.finished_at ? formatDateTime(latest.finished_at) : "종료 시각 없음"}
            </span>
          </div>
        ) : (
          <p className="rounded-md border border-[var(--color-border)] px-2 py-1.5 text-[var(--color-muted)]">
            아직 G-1 synthetic experiment run이 없습니다.
          </p>
        )}

        {presets.length > 0 ? (
          <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
            {presets.map((preset) => (
              <div
                key={preset.name}
                className="flex min-w-0 flex-col gap-1 rounded-md border border-[var(--color-border)] px-2 py-1.5"
              >
                <div className="flex min-w-0 flex-wrap items-start justify-between gap-2">
                  <span className="truncate font-medium text-[var(--color-fg)]">
                    {preset.name}
                  </span>
                  <Badge className="shrink-0 whitespace-nowrap" tone={runStatusTone(preset.latest_run_status)}>
                    {preset.latest_run_status ?? "not saved"}
                  </Badge>
                </div>
                <div className="flex flex-wrap items-center gap-1 text-[var(--color-muted)]">
                  <span>{sampleStatusLabel(preset.sample_status)}</span>
                  <span>· settled {preset.total_settled_count}</span>
                  {preset.missing_total_settled_count > 0 ? (
                    <span>· 부족 {preset.missing_total_settled_count}</span>
                  ) : null}
                  {preset.insufficient_operator_count > 0 ? (
                    <span>· 표본 부족 회사 {preset.insufficient_operator_count}</span>
                  ) : null}
                </div>
              </div>
            ))}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
