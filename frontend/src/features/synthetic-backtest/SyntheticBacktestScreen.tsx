import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useShellContext } from "@/app/dashboardContext";
import {
  fetchSyntheticOperators,
  runSyntheticBacktest,
  seedSyntheticOperators
} from "@/shared/api";
import { Badge, Button, Card, CardContent, CardHeader, CardTitle, Input, toastApi } from "@/shared/components/ui";
import { formatPercent } from "@/shared/lib";
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
        <ComparisonTable
          results={sortedResults}
          sortKey={sortKey}
          onSortKeyChange={setSortKey}
        />
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
  onSortKeyChange
}: {
  results: SyntheticBacktestOperatorResult[];
  sortKey: SortKey;
  onSortKeyChange: (key: SortKey) => void;
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
                <tr key={row.user_id} className="border-b border-[var(--color-border)]/60">
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

function compareNullable(a: number | null | undefined, b: number | null | undefined): number {
  const aIsNull = a === null || a === undefined;
  const bIsNull = b === null || b === undefined;
  if (aIsNull && bIsNull) return 0;
  if (aIsNull) return 1;
  if (bIsNull) return -1;
  return a - b;
}
