import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis
} from "recharts";
import { useShellContext } from "@/app/dashboardContext";
import {
  fetchSyntheticOperators,
  runSyntheticBacktest,
  seedSyntheticOperators
} from "@/shared/api";
import { Badge, Button, Card, CardContent, CardHeader, CardTitle, Input, toastApi } from "@/shared/components/ui";
import { formatCurrencyCompact, formatPercent } from "@/shared/lib";
import type {
  SyntheticBacktestOperatorResult,
  SyntheticBacktestRunRequest,
  SyntheticBacktestRunResponse,
  SyntheticOperatorItem
} from "@/shared/types/synthetic";

type SortKey = "win_rate_on_settled" | "bid_submission_rate" | "average_absolute_bid_rate_error";

export function SyntheticBacktestScreen() {
  const { session } = useShellContext();
  const queryClient = useQueryClient();

  const operators = useQuery({
    queryKey: ["synthetic", "operators"],
    queryFn: () => fetchSyntheticOperators(session?.token),
    enabled: Boolean(session?.token)
  });

  const seedMutation = useMutation({
    mutationFn: (purge: boolean) => seedSyntheticOperators({ purge }, session?.token),
    onSuccess: (data, purge) => {
      void queryClient.invalidateQueries({ queryKey: ["synthetic", "operators"] });
      toastApi.success({
        title: purge ? "리시드 완료" : "시드 완료",
        description: `12개 아키타입 중 ${data.seeded_count}건 upsert · ${data.purged_count}건 정리`
      });
    },
    onError: (err) =>
      toastApi.danger({
        title: "시드 실패",
        description: err instanceof Error ? err.message : "알 수 없는 오류"
      })
  });

  const [scenario, setScenario] = useState("base");
  const [limit, setLimit] = useState(100);
  const [category, setCategory] = useState("");

  const runMutation = useMutation<SyntheticBacktestRunResponse, Error, SyntheticBacktestRunRequest>({
    mutationFn: (payload) => runSyntheticBacktest(payload, session?.token),
    onError: (err) =>
      toastApi.danger({
        title: "백테스트 실행 실패",
        description: err instanceof Error ? err.message : "알 수 없는 오류"
      })
  });

  const [sortKey, setSortKey] = useState<SortKey>("win_rate_on_settled");
  const [drilldown, setDrilldown] = useState<SyntheticBacktestOperatorResult | null>(null);
  const sortedResults = useMemo(() => {
    const items = runMutation.data?.results ?? [];
    return [...items].sort((a, b) => compareNullable(b[sortKey], a[sortKey]));
  }, [runMutation.data, sortKey]);

  return (
    <section className="flex flex-col gap-4" aria-label="synthetic 백테스트 비교">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg font-semibold text-[var(--color-fg)]">가상 운영자 백테스트</h2>
        <span className="text-xs text-[var(--color-muted)]">
          win_rate는 가격 기준 추정 낙찰(would_have_won_price_only_count / settled_count). 실제 낙찰이 아님.
        </span>
      </header>

      <SeedPanel
        operators={operators.data?.operators ?? []}
        loading={operators.isPending}
        onSeed={(purge) => seedMutation.mutate(purge)}
        seedPending={seedMutation.isPending}
      />

      <Card>
        <CardHeader>
          <CardTitle>백테스트 실행</CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-1 gap-3 sm:grid-cols-4">
          <label className="flex flex-col gap-1 text-xs">
            <span className="text-[var(--color-muted)]">scenario</span>
            <Input value={scenario} onChange={(event) => setScenario(event.target.value)} aria-label="scenario" />
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span className="text-[var(--color-muted)]">limit</span>
            <Input
              type="number"
              min={1}
              max={1000}
              value={limit}
              onChange={(event) => setLimit(Number(event.target.value) || 1)}
              aria-label="limit"
              className="tabular-nums"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span className="text-[var(--color-muted)]">category (선택)</span>
            <Input
              value={category}
              onChange={(event) => setCategory(event.target.value)}
              aria-label="category"
              placeholder="software ..."
            />
          </label>
          <div className="flex items-end">
            <Button
              type="button"
              onClick={() =>
                runMutation.mutate({
                  scenario,
                  limit,
                  category: category || undefined
                })
              }
              disabled={runMutation.isPending || (operators.data?.operator_count ?? 0) === 0}
            >
              {runMutation.isPending ? "실행 중" : "실행"}
            </Button>
          </div>
        </CardContent>
      </Card>

      {runMutation.data ? (
        <>
          <WinRateBarChart results={sortedResults} />
          <ComparisonTable
            results={sortedResults}
            sortKey={sortKey}
            onSortKeyChange={setSortKey}
            onSelect={setDrilldown}
          />
        </>
      ) : null}

      {drilldown ? (
        <ArchetypeDrilldown row={drilldown} onClose={() => setDrilldown(null)} />
      ) : null}
    </section>
  );
}

function SeedPanel({
  operators,
  loading,
  onSeed,
  seedPending
}: {
  operators: SyntheticOperatorItem[];
  loading: boolean;
  onSeed: (purge: boolean) => void;
  seedPending: boolean;
}) {
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle>
          시드된 운영자
          <span className="ml-2 text-xs font-normal text-[var(--color-muted)]">
            ({operators.length}건)
          </span>
        </CardTitle>
        <div className="flex gap-2">
          <Button type="button" variant="outline" size="sm" onClick={() => onSeed(true)} disabled={seedPending}>
            리시드 (purge)
          </Button>
          <Button type="button" size="sm" onClick={() => onSeed(false)} disabled={seedPending}>
            시드
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {loading && operators.length === 0 ? (
          <p className="text-xs text-[var(--color-muted)]">불러오는 중…</p>
        ) : operators.length === 0 ? (
          <p className="text-xs text-[var(--color-muted)]">
            아직 시드되지 않았습니다. "시드" 버튼으로 12 아키타입을 upsert하세요.
          </p>
        ) : (
          <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3" aria-label="synthetic 운영자 카드">
            {operators.map((op) => (
              <li
                key={op.user_id}
                className="flex flex-col gap-1 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] p-2 text-xs"
              >
                <div className="flex items-center justify-between">
                  <strong className="text-[var(--color-fg)]">{op.display_name}</strong>
                  <Badge tone="muted">{op.slug}</Badge>
                </div>
                <span className="text-[var(--color-muted)]">{op.company ?? "-"}</span>
                <span className="text-[var(--color-muted)]">
                  threshold {formatPercent(op.bid_now_threshold)} / {formatPercent(op.review_threshold)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function ComparisonTable({
  results,
  sortKey,
  onSortKeyChange,
  onSelect
}: {
  results: SyntheticBacktestOperatorResult[];
  sortKey: SortKey;
  onSortKeyChange: (key: SortKey) => void;
  onSelect?: (row: SyntheticBacktestOperatorResult) => void;
}) {
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle>비교 결과</CardTitle>
        <label className="flex items-center gap-2 text-xs">
          정렬
          <select
            value={sortKey}
            onChange={(event) => onSortKeyChange(event.target.value as SortKey)}
            aria-label="정렬 기준"
            className="h-8 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-2 text-xs"
          >
            <option value="win_rate_on_settled">win_rate_on_settled</option>
            <option value="bid_submission_rate">bid_submission_rate</option>
            <option value="average_absolute_bid_rate_error">average_absolute_bid_rate_error</option>
          </select>
        </label>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-[var(--color-border)] text-left text-[var(--color-muted)]">
                <th className="py-1">운영자</th>
                <th className="py-1 text-right">candidates</th>
                <th className="py-1 text-right">settled</th>
                <th className="py-1 text-right">win_rate</th>
                <th className="py-1 text-right">submission_rate</th>
                <th className="py-1 text-right">avg |err|</th>
              </tr>
            </thead>
            <tbody>
              {results.map((row) => (
                <tr
                  key={row.user_id}
                  className="cursor-pointer border-b border-[var(--color-border)]/60 hover:bg-[var(--color-secondary)]"
                  onClick={() => onSelect?.(row)}
                  aria-label={`${row.display_name} 드릴다운`}
                >
                  <td className="py-1 pr-2">
                    <div className="flex flex-col">
                      <span className="font-medium text-[var(--color-fg)]">{row.display_name}</span>
                      <span className="text-[var(--color-muted)]">{row.slug}</span>
                    </div>
                  </td>
                  <td className="py-1 text-right tabular-nums">{row.candidate_count}</td>
                  <td className="py-1 text-right tabular-nums">{row.settled_count}</td>
                  <td className="py-1 text-right tabular-nums">{formatPercent(row.win_rate_on_settled)}</td>
                  <td className="py-1 text-right tabular-nums">{formatPercent(row.bid_submission_rate)}</td>
                  <td className="py-1 text-right tabular-nums">
                    {formatPercent(row.average_absolute_bid_rate_error)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}

function WinRateBarChart({ results }: { results: SyntheticBacktestOperatorResult[] }) {
  const data = results
    .map((row) => ({
      name: row.display_name,
      slug: row.slug,
      winRate: row.win_rate_on_settled,
      submissionRate: row.bid_submission_rate
    }))
    .filter((row) => row.winRate !== null && row.winRate !== undefined);

  if (data.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>승률 차트</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-xs text-[var(--color-muted)]">
            정산된 settled rows가 없어 승률 차트를 그릴 수 없습니다.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>아키타입별 승률 (가격 기준 추정)</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="h-56">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="slug" />
              <YAxis domain={[0, 1]} tickFormatter={(value) => formatPercent(value)} />
              <Tooltip formatter={(value) => formatPercent(Number(value))} />
              <Bar dataKey="winRate" name="win_rate_on_settled">
                {data.map((row, index) => (
                  <Cell key={row.slug} fill="var(--color-primary)" fillOpacity={1 - index * 0.04} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </CardContent>
    </Card>
  );
}

function ArchetypeDrilldown({
  row,
  onClose
}: {
  row: SyntheticBacktestOperatorResult;
  onClose: () => void;
}) {
  const items = row.settlement_items;
  const errorBuckets = useMemo(() => bucketizeBidRateErrors(items), [items]);

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle>
          {row.display_name} 드릴다운
          <span className="ml-2 text-xs font-normal text-[var(--color-muted)]">
            ({row.slug} · 샘플 {row.settlement_sample_count}/settled {row.settled_count})
          </span>
        </CardTitle>
        <Button type="button" variant="ghost" size="sm" onClick={onClose}>
          닫기
        </Button>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 text-xs">
        {items.length === 0 ? (
          <p className="text-[var(--color-muted)]">
            정산된 settlement row가 없어 드릴다운할 데이터가 없습니다.
          </p>
        ) : (
          <>
            <section>
              <h3 className="mb-1 text-[var(--color-fg)]">정산 샘플</h3>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-[var(--color-border)] text-left text-[var(--color-muted)]">
                      <th className="py-1">공고</th>
                      <th className="py-1 text-right">투찰가</th>
                      <th className="py-1 text-right">낙찰가</th>
                      <th className="py-1 text-right">|err|</th>
                      <th className="py-1 text-right">결과</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((item) => (
                      <tr
                        key={item.paper_bid_id ?? `${item.project_id}-${item.settled_at}`}
                        className="border-b border-[var(--color-border)]/60"
                      >
                        <td className="py-1 pr-2">
                          <span className="block truncate text-[var(--color-fg)]" title={item.project_title}>
                            {item.project_title || "—"}
                          </span>
                          <span className="text-[var(--color-muted)]">{item.category ?? "-"}</span>
                        </td>
                        <td className="py-1 text-right tabular-nums">
                          {item.bid_amount != null ? formatCurrencyCompact(item.bid_amount) : "-"}
                        </td>
                        <td className="py-1 text-right tabular-nums">
                          {item.winning_amount != null ? formatCurrencyCompact(item.winning_amount) : "-"}
                        </td>
                        <td className="py-1 text-right tabular-nums">
                          {formatPercent(item.absolute_bid_rate_error)}
                        </td>
                        <td className="py-1 text-right">
                          <Badge tone={item.would_have_won ? "healthy" : "muted"}>
                            {item.would_have_won ? "추정 낙찰" : "추정 실패"}
                          </Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <section>
              <h3 className="mb-1 text-[var(--color-fg)]">|bid_rate_error| 분포</h3>
              <div className="h-40">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={errorBuckets}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="label" />
                    <YAxis allowDecimals={false} />
                    <Tooltip />
                    <Bar dataKey="count" fill="var(--color-warn)" />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </section>
          </>
        )}
      </CardContent>
    </Card>
  );
}

function bucketizeBidRateErrors(items: SyntheticBacktestOperatorResult["settlement_items"]) {
  const buckets = [
    { label: "0–1%", min: 0, max: 0.01, count: 0 },
    { label: "1–3%", min: 0.01, max: 0.03, count: 0 },
    { label: "3–5%", min: 0.03, max: 0.05, count: 0 },
    { label: "5–10%", min: 0.05, max: 0.1, count: 0 },
    { label: "10%+", min: 0.1, max: Infinity, count: 0 }
  ];
  for (const item of items) {
    const err = item.absolute_bid_rate_error;
    if (err === null || err === undefined) continue;
    const bucket = buckets.find((b) => err >= b.min && err < b.max);
    if (bucket) bucket.count += 1;
  }
  return buckets;
}

function compareNullable(a: number | null | undefined, b: number | null | undefined): number {
  const aIsNull = a === null || a === undefined;
  const bIsNull = b === null || b === undefined;
  if (aIsNull && bIsNull) return 0;
  if (aIsNull) return 1;
  if (bIsNull) return -1;
  return a - b;
}
