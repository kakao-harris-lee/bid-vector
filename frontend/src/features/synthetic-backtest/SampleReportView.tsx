import { Badge, Card, CardContent, CardHeader, CardTitle, type BadgeTone } from "@/shared/components/ui";
import { LabeledStat } from "@/shared/components";
import { formatPercent } from "@/shared/lib";
import type { SyntheticSampleReport, SyntheticSampleReportGap, SyntheticSampleReportRow } from "@/shared/types/synthetic";

export interface SampleReportViewProps {
  report?: SyntheticSampleReport | null;
}

const DIMENSION_LABELS: Record<string, string> = {
  preset: "Preset",
  category: "카테고리",
  business_type: "업종",
  budget_band: "예산구간"
};

const BUDGET_BAND_LABELS: Record<string, string> = {
  lt_1eok: "1억 미만",
  "1eok_5eok": "1-5억",
  "5eok_10eok": "5-10억",
  "10eok_50eok": "10-50억",
  gte_50eok: "50억+"
};

function reportTone(report: SyntheticSampleReport): BadgeTone {
  if (report.report_status === "ready_for_reporting") return "healthy";
  if (report.report_status === "canonical_synthetic_mixed") return "critical";
  return "watch";
}

function reportLabel(report: SyntheticSampleReport): string {
  if (report.report_status === "ready_for_reporting") return "reporting ready";
  if (report.report_status === "canonical_synthetic_mixed") return "data mixed";
  return "sample gaps";
}

function dimensionLabel(dimension: string): string {
  return DIMENSION_LABELS[dimension] ?? dimension;
}

function rowLabel(row: SyntheticSampleReportRow): string {
  if (row.dimension === "budget_band") return BUDGET_BAND_LABELS[row.key] ?? row.label ?? row.key;
  return row.label ?? row.key;
}

function formatNullablePercent(value: number | null | undefined): string {
  return typeof value === "number" ? formatPercent(value) : "-";
}

export function SampleReportView({ report }: SampleReportViewProps) {
  if (!report) return null;

  const gapRows = report.lacking_groups ?? [];
  const syntheticLeaks = report.non_synthetic_operator_slugs ?? [];

  return (
    <Card aria-label="G-1 sample report">
      <CardHeader className="flex-row items-center justify-between gap-2">
        <CardTitle>G-1 sample report</CardTitle>
        <Badge tone={reportTone(report)}>{reportLabel(report)}</Badge>
      </CardHeader>
      <CardContent className="flex flex-col gap-4 text-xs">
        <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
          <LabeledStat variant="card" label="preset" value={report.preset_name ?? "custom"} />
          <LabeledStat variant="card"
            label="repeatable"
            value={report.ready_for_repeatable_reporting ? "ready" : "blocked"}
          />
          <LabeledStat variant="card" label="group target" value={String(report.group_sample_target)} />
          <LabeledStat variant="card" label="synthetic only" value={report.synthetic_only ? "yes" : "no"} />
        </div>

        {syntheticLeaks.length > 0 ? (
          <div
            role="alert"
            className="rounded-md border border-[var(--color-danger)] px-3 py-2 text-[var(--color-fg)]"
          >
            canonical mix: {syntheticLeaks.join(", ")}
          </div>
        ) : null}

        <GapList gaps={gapRows} />

        <ReportRows title="Preset" rows={report.by_preset ?? []} />
        <ReportRows title="카테고리" rows={report.by_category ?? []} />
        <ReportRows title="업종" rows={report.by_business_type ?? []} />
        <ReportRows title="예산구간" rows={report.by_budget_band ?? []} />
      </CardContent>
    </Card>
  );
}

function GapList({ gaps }: { gaps: SyntheticSampleReportGap[] }) {
  if (gaps.length === 0) {
    return (
      <p className="rounded-md border border-[var(--color-border)] px-3 py-2 text-[var(--color-fg)]">
        모든 보고 그룹이 정산 샘플 기준을 충족했습니다.
      </p>
    );
  }

  return (
    <section aria-label="샘플 부족 그룹">
      <h3 className="mb-1 font-medium text-[var(--color-fg)]">샘플 부족</h3>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-[var(--color-border)] text-left text-[var(--color-muted)]">
              <th className="py-1">구분</th>
              <th className="py-1">키</th>
              <th className="py-1 text-right">정산</th>
              <th className="py-1 text-right">부족</th>
            </tr>
          </thead>
          <tbody>
            {gaps.map((gap) => (
              <tr key={`${gap.dimension}:${gap.key}`} className="border-b border-[var(--color-border)]/60">
                <td className="py-1 pr-2">{dimensionLabel(gap.dimension)}</td>
                <td className="py-1 pr-2 text-[var(--color-fg)]">{gap.key}</td>
                <td className="py-1 text-right tabular-nums">
                  {gap.settled_count}/{gap.sample_target}
                </td>
                <td className="py-1 text-right tabular-nums text-[var(--color-fg)]">
                  {gap.missing_settled_count}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function ReportRows({ title, rows }: { title: string; rows: SyntheticSampleReportRow[] }) {
  if (rows.length === 0) return null;

  return (
    <section aria-label={`${title} report`}>
      <h3 className="mb-1 font-medium text-[var(--color-fg)]">{title}</h3>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-[var(--color-border)] text-left text-[var(--color-muted)]">
              <th className="py-1">그룹</th>
              <th className="py-1 text-right">정산</th>
              <th className="py-1 text-right">가격 근접</th>
              <th className="py-1 text-right">평균 |오차|</th>
              <th className="py-1 text-right">상태</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={`${row.dimension}:${row.key}`} className="border-b border-[var(--color-border)]/60">
                <td className="py-1 pr-2 text-[var(--color-fg)]">{rowLabel(row)}</td>
                <td className="py-1 text-right tabular-nums">
                  {row.settled_count}/{row.sample_target}
                </td>
                <td className="py-1 text-right tabular-nums">
                  {formatNullablePercent(row.est_price_close_rate)}
                </td>
                <td className="py-1 text-right tabular-nums">
                  {formatNullablePercent(row.avg_abs_bid_rate_error)}
                </td>
                <td className="py-1 text-right">
                  <Badge tone={row.sample_status === "sufficient" ? "healthy" : "watch"}>
                    {row.sample_status === "sufficient" ? "ready" : `-${row.missing_settled_count}`}
                  </Badge>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
