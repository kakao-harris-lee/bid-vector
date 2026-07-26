import { Badge, Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import { LabeledStat } from "@/shared/components";
import { formatDateTime } from "@/shared/lib";
import type {
  G2EvidenceSectionSummary,
  G2EvidenceSummaryResponse
} from "@/shared/types/operations";
import { g2StatusLabel, g2StatusTone } from "./helpers";

export function G2EvidenceCard({
  data,
  loading,
  error
}: {
  data: G2EvidenceSummaryResponse | null;
  loading: boolean;
  error: Error | null;
}) {
  const sections = [
    { key: "smoke", label: "Smoke 증적", summary: data?.smoke ?? null },
    { key: "strategy_monitor", label: "Strategy monitor", summary: data?.strategy_monitor ?? null },
    { key: "decision_experiments", label: "Decision experiment", summary: data?.decision_experiments ?? null },
    { key: "synthetic_experiments", label: "Synthetic experiment", summary: data?.synthetic_experiments ?? null },
    { key: "notifications", label: "알림 증적", summary: data?.notifications ?? null }
  ];
  return (
    <Card aria-label="G-2 증적 요약">
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <CardTitle className="break-words leading-snug">
            G-2 evidence / blocking gaps
          </CardTitle>
          <p className="mt-1 break-words text-xs text-[var(--color-muted)]">
            선택한 operator 기준으로 증적 범위와 남은 차단 gap을 분리 확인합니다.
          </p>
        </div>
        {data ? (
          <Badge className="shrink-0 whitespace-nowrap" tone={g2StatusTone(data.evidence_status)}>
            {g2StatusLabel(data.evidence_status)}
          </Badge>
        ) : (
          <Badge className="shrink-0 whitespace-nowrap" tone="muted">미연결</Badge>
        )}
      </CardHeader>
      <CardContent className="flex flex-col gap-3 text-xs">
        {loading ? (
          <p className="text-[var(--color-muted)]">G-2 증적을 불러오는 중…</p>
        ) : null}

        {error ? (
          <p
            role="alert"
            className="rounded-md border border-[var(--color-danger)] bg-[color-mix(in_oklch,var(--color-danger),white_85%)] px-2 py-1.5 text-[var(--color-danger)]"
          >
            {error.message}
          </p>
        ) : null}

        {!loading && !error && !data ? (
          <p className="rounded-md border border-[var(--color-border)] px-2 py-1.5 text-[var(--color-muted)]">
            G-2 evidence API가 아직 연결되지 않았습니다. Agent A 응답이 들어오면 이 영역에 operator별 증적 상태가 표시됩니다.
          </p>
        ) : null}

        {data ? (
          <>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-4">
              <LabeledStat
                label="점검 operator"
                value={
                  data.current_operator_username ??
                  (data.current_operator_id ? `#${data.current_operator_id}` : "-")
                }
              />
              <LabeledStat label="증적 window" value={`${data.window_days}일`} />
              <LabeledStat label="blocking gaps" value={data.blocking_gaps.length.toString()} />
              <LabeledStat
                label="생성 시각"
                value={data.generated_at ? formatDateTime(data.generated_at) : "-"}
              />
            </div>

            {data.blocking_gaps.length > 0 ? (
              <div className="flex flex-col gap-1" aria-label="G-2 차단 gap">
                <span className="font-medium text-[var(--color-fg)]">차단 gap</span>
                <ul className="flex flex-col gap-1">
                  {data.blocking_gaps.map((gap, index) => (
                    <li
                      key={`${gap}-${index}`}
                      className="break-words rounded-md bg-[color-mix(in_oklch,var(--color-warn),white_84%)] px-2 py-1 text-[color-mix(in_oklch,var(--color-warn),black_45%)]"
                    >
                      {gap}
                    </li>
                  ))}
                </ul>
              </div>
            ) : (
              <p className="text-[var(--color-muted)]">차단 gap 없음</p>
            )}

            <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
              {sections.map((section) => (
                <EvidenceDomainRow
                  key={section.key}
                  label={section.label}
                  summary={section.summary}
                />
              ))}
            </div>

            {(data.warnings ?? []).length > 0 ? (
              <div className="flex flex-wrap gap-1" aria-label="G-2 증적 경고">
                {(data.warnings ?? []).map((warning, index) => (
                  <Badge key={`${warning}-${index}`} className="max-w-full whitespace-normal break-words" tone="watch">
                    {warning}
                  </Badge>
                ))}
              </div>
            ) : null}
          </>
        ) : null}
      </CardContent>
    </Card>
  );
}

function EvidenceDomainRow({
  label,
  summary
}: {
  label: string;
  summary: G2EvidenceSectionSummary | null;
}) {
  const status = summary?.evidence_status ?? summary?.status ?? "missing";
  const count = summary?.evidence_count ?? summary?.run_count ?? null;
  return (
    <div className="flex min-w-0 flex-col gap-1 rounded-md border border-[var(--color-border)] px-2 py-1.5">
      <div className="flex min-w-0 flex-wrap items-start justify-between gap-2">
        <span className="truncate font-medium text-[var(--color-fg)]">{label}</span>
        <Badge className="shrink-0 whitespace-nowrap" tone={g2StatusTone(status)}>
          {g2StatusLabel(status)}
        </Badge>
      </div>
      <p className="break-words text-[var(--color-muted)]">
        {summary?.detail ?? summary?.summary ?? "증적 없음"}
      </p>
      <div className="flex flex-wrap gap-1 text-[var(--color-muted)]">
        {typeof count === "number" ? <span>evidence {count}</span> : null}
        {summary?.latest_at ? <span>latest {formatDateTime(summary.latest_at)}</span> : null}
        {(summary?.blocking_gaps ?? []).length > 0 ? (
          <span>gap {(summary?.blocking_gaps ?? []).length}</span>
        ) : null}
      </div>
    </div>
  );
}
