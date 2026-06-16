import { useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis
} from "recharts";
import { useShellContext } from "@/app/dashboardContext";
import { useAccuracyReportQuery } from "./hooks";
import { Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import { formatCurrency, formatDateTime, formatPercent } from "@/shared/lib";
import type {
  AccuracyReportCategory,
  AccuracyReportErrorBin,
  AccuracyReportItem,
  AccuracyReportSummary,
  AccuracyReportTrendBucket
} from "@/shared/types/accuracyReport";

const REPORT_DAYS_OPTIONS = [7, 30, 90] as const;
type ReportDays = (typeof REPORT_DAYS_OPTIONS)[number];

const ITEM_ROW_CAP = 50;
const DASH = "—";

/** 0~1 비율을 % 문자열로. null/undefined면 데이터 부재 대시. */
function rateOrDash(value?: number | null): string {
  if (value === null || value === undefined) return DASH;
  return formatPercent(value);
}

/** 정수 카운트를 천단위 콤마로. */
function formatCount(value: number): string {
  return value.toLocaleString("ko-KR");
}

export function AccuracyReportScreen() {
  const { session } = useShellContext();
  const [days, setDays] = useState<ReportDays>(90);
  const report = useAccuracyReportQuery(session, {
    days,
    limit: 500,
    trendBucketDays: 7
  });

  const data = report.data;
  const summary = data?.summary;
  const hasSamples = (summary?.recommendation_sample_count ?? 0) > 0;
  const notes = data?.notes ?? [];

  return (
    <section className="flex flex-col gap-4" aria-label="정확도 리포트">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="flex flex-col gap-1">
          <h2 className="text-lg font-semibold text-[var(--color-fg)]">
            추천 vs 실제 통합 정확도 리포트
          </h2>
          <p className="text-xs text-[var(--color-muted)]">
            추천가(recommended_amount)와 실제 낙찰가(winning_amount)의 실측 비교 — 정산 완료 건만
            포함합니다.
          </p>
        </div>
        <label className="flex items-center gap-2 text-xs">
          기간
          <select
            value={days}
            onChange={(event) => setDays(Number(event.target.value) as ReportDays)}
            aria-label="리포트 기간 (일)"
            className="h-8 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-2 text-xs"
          >
            {REPORT_DAYS_OPTIONS.map((value) => (
              <option key={value} value={value}>
                {value}일
              </option>
            ))}
          </select>
        </label>
      </header>

      {report.isPending && !data ? (
        <p className="text-sm text-[var(--color-muted)]">불러오는 중…</p>
      ) : null}

      {report.error ? (
        <p
          role="alert"
          className="rounded-md border border-[var(--color-danger)] bg-[color-mix(in_oklch,var(--color-danger),white_82%)] px-3 py-2 text-sm text-[var(--color-danger)]"
        >
          {report.error.message ?? "정확도 리포트를 불러오지 못했습니다."}
        </p>
      ) : null}

      {data && summary ? (
        hasSamples ? (
          <>
            <SummaryCards summary={summary} />
            <ErrorDistributionCard bins={data.error_distribution ?? []} />
            <PerCategoryCard categories={data.per_category ?? []} />
            <TrendCard buckets={data.time_trend ?? []} />
            <ItemsCard items={data.items ?? []} />
          </>
        ) : (
          <Card>
            <CardContent className="py-6">
              <p className="text-sm text-[var(--color-muted)]">
                정산 완료된 추천 비교 표본이 아직 없습니다.
              </p>
            </CardContent>
          </Card>
        )
      ) : null}

      {notes.length > 0 ? <CaveatCard notes={notes} /> : null}
    </section>
  );
}

function SummaryCards({ summary }: { summary: AccuracyReportSummary }) {
  return (
    <section
      className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3"
      aria-label="요약 지표"
    >
      <StatCard label="추천 표본수" value={`${formatCount(summary.recommendation_sample_count)}건`} />
      <StatCard
        label="평균 추천 오차율"
        value={rateOrDash(summary.average_recommendation_error_rate)}
        hint="낮을수록 좋음 · error = |추천가−실제낙찰가|/실제낙찰가"
      />
      <StatCard
        label="추천이 예측보다 나은 건수"
        value={`${formatCount(summary.recommendation_better_than_prediction_count)}건`}
        hint="추천가 오차 < 예측가 오차 인 표본 수"
      />
      <StatCard
        label="±1% 적중률"
        value={rateOrDash(summary.within_1pct_rate)}
        sub={`${formatCount(summary.within_1pct_count)}건`}
      />
      <StatCard
        label="±3% 적중률"
        value={rateOrDash(summary.within_3pct_rate)}
        sub={`${formatCount(summary.within_3pct_count)}건`}
      />
      <StatCard
        label="±5% 적중률"
        value={rateOrDash(summary.within_5pct_rate)}
        sub={`${formatCount(summary.within_5pct_count)}건`}
      />
    </section>
  );
}

function StatCard({
  label,
  value,
  sub,
  hint
}: {
  label: string;
  value: string;
  sub?: string;
  hint?: string;
}) {
  return (
    <Card>
      <CardHeader className="flex-row items-baseline justify-between">
        <CardTitle className="text-sm">{label}</CardTitle>
        {hint ? (
          <span className="text-[10px] text-[var(--color-muted)]" title={hint}>
            ⓘ
          </span>
        ) : null}
      </CardHeader>
      <CardContent className="flex flex-col gap-0.5">
        <strong className="text-2xl tabular-nums text-[var(--color-fg)]">{value}</strong>
        {sub ? <span className="text-xs text-[var(--color-muted)]">{sub}</span> : null}
      </CardContent>
    </Card>
  );
}

function ErrorDistributionCard({ bins }: { bins: AccuracyReportErrorBin[] }) {
  const data = bins.map((bin) => ({ label: bin.bin_label, count: bin.count }));
  return (
    <Card aria-label="오차 분포">
      <CardHeader>
        <CardTitle>오차 분포</CardTitle>
      </CardHeader>
      <CardContent>
        {bins.length === 0 ? (
          <p className="text-xs text-[var(--color-muted)]">분포를 그릴 표본이 없습니다.</p>
        ) : (
          <div className="flex flex-col gap-3">
            <div className="h-56">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={data}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="label" />
                  <YAxis allowDecimals={false} />
                  <Tooltip />
                  <Bar dataKey="count" name="표본수" fill="var(--color-primary)" />
                </BarChart>
              </ResponsiveContainer>
            </div>
            <ul className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-[var(--color-muted)]">
              {bins.map((bin) => (
                <li key={bin.bin_label} className="tabular-nums">
                  <span className="text-[var(--color-fg)]">{bin.bin_label}</span>{" "}
                  {formatCount(bin.count)}건 ({rateOrDash(bin.rate)})
                </li>
              ))}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function PerCategoryCard({ categories }: { categories: AccuracyReportCategory[] }) {
  return (
    <Card aria-label="분야별 정확도">
      <CardHeader>
        <CardTitle>분야별 정확도</CardTitle>
      </CardHeader>
      <CardContent>
        {categories.length === 0 ? (
          <p className="text-xs text-[var(--color-muted)]">분야별 표본이 없습니다.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-[var(--color-border)] text-left text-[var(--color-muted)]">
                  <th className="py-1.5 pr-2 font-medium">분야</th>
                  <th className="py-1.5 pr-2 text-right font-medium">표본수</th>
                  <th className="py-1.5 pr-2 text-right font-medium">평균 추천 오차율</th>
                  <th className="py-1.5 text-right font-medium">±3% 적중률</th>
                </tr>
              </thead>
              <tbody>
                {categories.map((category) => (
                  <tr
                    key={category.category}
                    className="border-b border-[var(--color-border)] last:border-0"
                  >
                    <td className="py-1.5 pr-2 text-[var(--color-fg)]">{category.category}</td>
                    <td className="py-1.5 pr-2 text-right tabular-nums">
                      {formatCount(category.sample_count)}
                    </td>
                    <td className="py-1.5 pr-2 text-right tabular-nums">
                      {rateOrDash(category.average_recommendation_error_rate)}
                    </td>
                    <td className="py-1.5 text-right tabular-nums">
                      {rateOrDash(category.within_3pct_rate)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function TrendCard({ buckets }: { buckets: AccuracyReportTrendBucket[] }) {
  // 추세는 버킷이 2개 이상일 때만 의미가 있다.
  if (buckets.length < 2) return null;
  const data = buckets.map((bucket) => ({
    period: formatDateTime(bucket.period_start),
    errorRate: bucket.average_recommendation_error_rate,
    sampleCount: bucket.sample_count
  }));
  return (
    <Card aria-label="추세">
      <CardHeader>
        <CardTitle>추세 (평균 추천 오차율)</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="h-56">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="period" />
              <YAxis tickFormatter={(value) => formatPercent(value)} />
              <Tooltip formatter={(value) => formatPercent(Number(value))} />
              <Line
                type="monotone"
                dataKey="errorRate"
                name="평균 추천 오차율"
                stroke="var(--color-primary)"
                dot
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </CardContent>
    </Card>
  );
}

function ItemsCard({ items }: { items: AccuracyReportItem[] }) {
  const visible = items.slice(0, ITEM_ROW_CAP);
  return (
    <Card aria-label="상세">
      <CardHeader className="flex-row items-baseline justify-between">
        <CardTitle>상세</CardTitle>
        {items.length > ITEM_ROW_CAP ? (
          <span className="text-[11px] text-[var(--color-muted)]">
            상위 {formatCount(ITEM_ROW_CAP)}건 표시 (총 {formatCount(items.length)}건)
          </span>
        ) : (
          <span className="text-[11px] text-[var(--color-muted)]">총 {formatCount(items.length)}건</span>
        )}
      </CardHeader>
      <CardContent>
        {visible.length === 0 ? (
          <p className="text-xs text-[var(--color-muted)]">상세 표본이 없습니다.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-[var(--color-border)] text-left text-[var(--color-muted)]">
                  <th className="py-1.5 pr-2 font-medium">공고명</th>
                  <th className="py-1.5 pr-2 text-right font-medium">실제 낙찰가</th>
                  <th className="py-1.5 pr-2 text-right font-medium">추천가</th>
                  <th className="py-1.5 pr-2 text-right font-medium">추천 오차율</th>
                  <th className="py-1.5 pr-2 font-medium">분야</th>
                  <th className="py-1.5 font-medium">공고일</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((item) => (
                  <tr
                    key={item.tender_result_id}
                    className="border-b border-[var(--color-border)] last:border-0"
                  >
                    <td className="max-w-[16rem] truncate py-1.5 pr-2 text-[var(--color-fg)]" title={item.project_title}>
                      {item.project_title}
                    </td>
                    <td className="py-1.5 pr-2 text-right tabular-nums">
                      {formatCurrency(item.winning_amount)}
                    </td>
                    <td className="py-1.5 pr-2 text-right tabular-nums">
                      {formatCurrency(item.recommended_amount)}
                    </td>
                    <td className="py-1.5 pr-2 text-right tabular-nums">
                      {rateOrDash(item.recommendation_error_rate)}
                    </td>
                    <td className="py-1.5 pr-2 text-[var(--color-muted)]">
                      {item.category ?? "(미분류)"}
                    </td>
                    <td className="py-1.5 text-[var(--color-muted)]">
                      {item.announced_at ? formatDateTime(item.announced_at) : DASH}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function CaveatCard({ notes }: { notes: string[] }) {
  return (
    <Card aria-label="해석 주의사항">
      <CardHeader>
        <CardTitle className="text-sm">해석 주의사항</CardTitle>
      </CardHeader>
      <CardContent>
        <ul className="flex list-disc flex-col gap-1 pl-4 text-xs text-[var(--color-muted)]">
          {notes.map((note, index) => (
            <li key={index}>{note}</li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
